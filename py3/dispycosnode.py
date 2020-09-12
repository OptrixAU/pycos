#!/usr/bin/python3

"""
This file is part of pycos project. See https://pycos.sourceforge.io for details.

This program can be used to start dispycos server processes so dispycos
scheduler (see 'dispycos.py') can send clients to these server processes
for executing distributed communicating proceses (tasks).

See 'dispycos_*.py' files for example use cases.
"""

__author__ = "Giridhar Pemmasani (pgiri@yahoo.com)"
__copyright__ = "Copyright (c) 2014 Giridhar Pemmasani"
__license__ = "Apache 2.0"
__url__ = "https://pycos.sourceforge.io"


def _dispycos_server_proc():
    # task
    """Server process receives a client and runs tasks for it.
    """

    import os
    import shutil
    import traceback
    import sys
    import time

    from pycos.dispycos import MinPulseInterval, MaxPulseInterval, \
        DispycosNodeInfo, DispycosNodeAvailInfo, Scheduler
    from pycos.netpycos import Task, SysTask, Location, MonitorException, deserialize, logger
    global _DispycosJob_
    from pycos.dispycos import _DispycosJob_

    for _dispycos_var in ('_dispycos_server_process', '_dispycos_server_proc'):
        globals().pop(_dispycos_var, None)
    _dispycos_scheduler = pycos.Pycos.instance()
    _dispycos_task = pycos.Pycos.cur_task()
    _dispycos_config = yield _dispycos_task.receive()
    if not isinstance(_dispycos_config, dict):
        raise StopIteration(-1)
    _dispycos_node_task = _dispycos_config.pop('node_task')
    if not isinstance(_dispycos_node_task, Task):
        logger.warning('%s: invalid node task: %s',
                       _dispycos_task.location, type(_dispycos_node_task))
        raise StopIteration(-1)
    _dispycos_auth = _dispycos_config.pop('auth', None)
    if _dispycos_config['min_pulse_interval'] > 0:
        MinPulseInterval = _dispycos_config['min_pulse_interval']
    if _dispycos_config['max_pulse_interval'] > 0:
        MaxPulseInterval = _dispycos_config['max_pulse_interval']
    _dispycos_busy_time = _dispycos_config.pop('busy_time')
    pycos.netpycos.MsgTimeout = pycos.MsgTimeout = _dispycos_config.pop('msg_timeout')

    _dispycos_task.register('_dispycos_server')
    if ((yield _dispycos_node_task.deliver({'req': 'server_task',  'auth': _dispycos_auth,
                                            'iid': _dispycos_config['iid'], 'task': _dispycos_task,
                                            'server_id': _dispycos_config['sid'],
                                            'pid': os.getpid()}, timeout=pycos.MsgTimeout)) != 1):
        raise StopIteration(-1)
    _dispycos_var = yield _dispycos_task.recv(timeout=pycos.MsgTimeout)
    if (isinstance(_dispycos_var, dict) and _dispycos_var.get('auth') == _dispycos_auth and
        _dispycos_var.get('node_task') == _dispycos_node_task):
        pass
    else:
        raise StopIteration(-1)

    _dispycos_name = _dispycos_scheduler.name
    os.chdir(_dispycos_config['dest_path'])
    _dispycos_scheduler.dest_path = _dispycos_config['dest_path']
    sys.path.insert(0, _dispycos_config['dest_path'])

    _dispycos_peers = set()
    _dispycos_peers.add(_dispycos_node_task.location)

    for _dispycos_var in ['min_pulse_interval', 'max_pulse_interval']:
        del _dispycos_config[_dispycos_var]

    _dispycos_busy_time.value = int(time.time())
    logger.info('dispycos server %s started at %s; client files will be saved in "%s"',
                _dispycos_config['sid'], _dispycos_task.location, _dispycos_config['dest_path'])
    _dispycos_req = _dispycos_reply_task = _dispycos_msg = None
    _dispycos_peer_status = _dispycos_monitor_task = _dispycos_monitor_proc = _dispycos_job = None
    _dispycos_restart = False
    _dispycos_job_tasks = set()
    _dispycos_jobs_done = pycos.Event()

    def _dispycos_timer_proc(task=None):
        task.set_daemon()
        pulse_interval = _dispycos_config['pulse_interval']
        while 1:
            yield task.sleep(pulse_interval)
            if _dispycos_job_tasks:
                _dispycos_busy_time.value = int(time.time())

    def _dispycos_peer_status(task=None):
        task.set_daemon()
        while 1:
            status = yield task.receive()
            if not isinstance(status, pycos.PeerStatus):
                logger.warning('Invalid peer status %s ignored', type(status))
                continue
            if status.status == pycos.PeerStatus.Offline:
                if (_dispycos_scheduler_task and
                    _dispycos_scheduler_task.location == status.location):
                    _dispycos_task.send({'req': 'close', 'auth': _dispycos_auth})
            else:  # status.status == pycos.PeerStatus.Online
                if (status.location not in _dispycos_peers):
                    def dispycos_peer(location, task=None):
                        peer = yield SysTask.locate('_dispycos_server', location,
                                                    timeout=pycos.MsgTimeout)
                        if isinstance(peer, SysTask):
                            # TODO: accept only if serving same client?
                            _dispycos_peers.add(location)
                            # TODO: inform user tasks?
                        elif status.location not in _dispycos_peers:
                            Task(_dispycos_scheduler.close_peer, location)
                    SysTask(dispycos_peer, status.location)

    def _dispycos_monitor_proc(task=None):
        task.set_daemon()
        while 1:
            msg = yield task.receive()
            if isinstance(msg, MonitorException):
                logger.debug('task %s done at %s', msg.args[0], task.location)
                _dispycos_job_tasks.discard(msg.args[0])
                if not _dispycos_job_tasks:
                    _dispycos_jobs_done.set()
                try:
                    pycos.serialize(msg.args[1][1])
                except Exception:
                    msg.args = (msg.args[0], (msg.args[1][0], type(msg.args[1][1])))
                _dispycos_scheduler_task.send(msg)
            else:
                logger.warning('invalid message to monitor ignored: %s', type(msg))

    def _dispycos_quit_wrapper():
        task = _dispycos_task
        auth = _dispycos_auth

        def wrapped(terminate=False, restart=False):
            task.send({'req': 'terminate' if terminate else 'quit', 'restart': bool(restart),
                       'auth': auth})
        return wrapped

    globals()['dispycos_close_server'] = _dispycos_quit_wrapper()
    del _dispycos_quit_wrapper

    _dispycos_var = deserialize(_dispycos_config['scheduler_location'])
    if (yield _dispycos_scheduler.peer(_dispycos_var)):
        logger.warning('%s could not communicate with scheduler', _dispycos_task.location)
        raise StopIteration(-1)
    _dispycos_peers.add(_dispycos_var)
    _dispycos_scheduler_task = yield SysTask.locate('dispycos_status', location=_dispycos_var,
                                                    timeout=5)
    if not isinstance(_dispycos_scheduler_task, SysTask):
        logger.warning('%s could not locate scheduler', _dispycos_task.location)
        raise StopIteration(-1)
    _dispycos_var = deserialize(_dispycos_config['client_location'])
    if (yield _dispycos_scheduler.peer(_dispycos_var)):
        logger.warning('%s could not communicate with commputation', _dispycos_task.location)
        raise StopIteration(-1)
    _dispycos_peers.add(_dispycos_var)

    _dispycos_scheduler.peer_status(SysTask(_dispycos_peer_status))
    _dispycos_scheduler_task.send({'status': Scheduler.ServerDiscovered, 'task': _dispycos_task,
                                   'name': _dispycos_name, 'auth': _dispycos_auth})
    for _dispycos_var in _dispycos_config.pop('peers', []):
        Task(_dispycos_scheduler.peer, deserialize(_dispycos_var))

    if _dispycos_config['server_setup']:
        if _dispycos_config['disable_servers']:
            while 1:
                _dispycos_msg = yield _dispycos_task.receive()
                if ((not isinstance(_dispycos_msg, dict)) or
                    _dispycos_auth != _dispycos_msg.get('auth', None)):
                    logger.warning('Ignoring invalid request to run server setup')
                    continue
                _dispycos_req = _dispycos_msg.get('req', None)
                if _dispycos_req == 'enable_server':
                    _dispycos_var = pycos.deserialize(_dispycos_msg['setup_args'])
                    break
                elif _dispycos_req == 'terminate' or _dispycos_req == 'quit':
                    if _dispycos_scheduler_task:
                        _dispycos_scheduler_task.send({'status': Scheduler.ServerClosed,
                                                       'location': _dispycos_task.location,
                                                       'auth': _dispycos_auth})
                    raise StopIteration(0)
                else:
                    logger.warning('Ignoring invalid request to run server setup')
        else:
            _dispycos_var = ()
        _dispycos_var = yield pycos.Task(globals()[_dispycos_config['server_setup']],
                                         *_dispycos_var).finish()
        if _dispycos_var != 0:
            logger.debug('dispycos server %s @ %s setup failed', _dispycos_config['sid'],
                         _dispycos_task.location)
            raise StopIteration(_dispycos_var)
        _dispycos_config['server_setup'] = None
    _dispycos_scheduler_task.send({'status': Scheduler.ServerInitialized, 'task': _dispycos_task,
                                   'name': _dispycos_name, 'auth': _dispycos_auth})

    _dispycos_timer_task = Task(_dispycos_timer_proc)
    _dispycos_monitor_task = SysTask(_dispycos_monitor_proc)
    logger.debug('dispycos server "%s": Client "%s" from %s', _dispycos_name,
                 _dispycos_auth, _dispycos_scheduler_task.location)

    while 1:
        _dispycos_msg = yield _dispycos_task.receive()
        try:
            _dispycos_req = _dispycos_msg.get('req', None)
        except Exception:
            continue

        if _dispycos_req == 'run':
            _dispycos_reply_task = _dispycos_msg.get('reply_task', None)
            _dispycos_job = _dispycos_msg.get('job', None)
            if (not isinstance(_dispycos_reply_task, Task) or
                _dispycos_auth != _dispycos_msg.get('auth', None)):
                logger.warning('invalid run: %s', type(_dispycos_reply_task))
                if isinstance(_dispycos_reply_task, Task):
                    _dispycos_reply_task.send(None)
                continue
            try:
                if _dispycos_job.code:
                    exec(_dispycos_job.code, globals())
                _dispycos_job.args = deserialize(_dispycos_job.args)
                _dispycos_job.kwargs = deserialize(_dispycos_job.kwargs)
            except Exception:
                logger.debug('invalid client to run')
                _dispycos_var = (sys.exc_info()[0], _dispycos_job.name, traceback.format_exc())
                _dispycos_reply_task.send(_dispycos_var)
            else:
                Task._pycos._lock.acquire()
                try:
                    _dispycos_var = Task(globals()[_dispycos_job.name],
                                         *(_dispycos_job.args), **(_dispycos_job.kwargs))
                except Exception:
                    _dispycos_var = (sys.exc_info()[0], _dispycos_job.name, traceback.format_exc())
                else:
                    _dispycos_job_tasks.add(_dispycos_var)
                    _dispycos_jobs_done.clear()
                    logger.debug('task %s created at %s', _dispycos_var, _dispycos_task.location)
                    _dispycos_var.notify(_dispycos_monitor_task)
                _dispycos_reply_task.send(_dispycos_var)
                Task._pycos._lock.release()

        elif _dispycos_req == 'close' or _dispycos_req == 'quit':
            if _dispycos_msg.get('auth', None) != _dispycos_auth:
                continue
            if _dispycos_scheduler_task:
                _dispycos_scheduler_task.send({'status': Scheduler.ServerClosed,
                                              'location': _dispycos_task.location,
                                               'auth': _dispycos_auth})
            while _dispycos_job_tasks:
                logger.debug('dispycos server "%s": Waiting for %s tasks to terminate before '
                             'closing client', _dispycos_name, len(_dispycos_job_tasks))
                if (yield _dispycos_jobs_done.wait(timeout=5)):
                    break
                _dispycos_var = yield _dispycos_task.recv(timeout=0)
                if (isinstance(_dispycos_var, dict) and
                    (_dispycos_var.get('req', None) == 'terminate') and
                    (_dispycos_var.get('auth', None) == _dispycos_auth)):
                    break
            if _dispycos_msg.get('restart', False):
                _dispycos_restart = True
            break

        elif _dispycos_req == 'terminate':
            if _dispycos_msg.get('auth', None) != _dispycos_auth:
                continue
            if _dispycos_scheduler_task:
                _dispycos_scheduler_task.send({'status': Scheduler.ServerClosed,
                                               'location': _dispycos_task.location,
                                               'auth': _dispycos_auth})
            if _dispycos_msg.get('restart', False):
                _dispycos_restart = True
            break

        elif _dispycos_req == 'status':
            if _dispycos_msg.get('auth', None) != _dispycos_auth:
                continue
            if _dispycos_scheduler_task:
                print('  dispycos server "%s" @ %s with PID %s running %d tasks for %s' %
                      (_dispycos_name, _dispycos_task.location, os.getpid(),
                       len(_dispycos_job_tasks), _dispycos_scheduler_task.location))
            else:
                print('  dispycos server "%s" @ %s with PID %s not used by any client' %
                      (_dispycos_name, _dispycos_task.location, os.getpid()))

        elif _dispycos_req == 'peers':
            if _dispycos_msg.get('auth', None) != _dispycos_auth:
                continue
            for _dispycos_var in _dispycos_msg.get('peers', []):
                pycos.Task(_dispycos_scheduler.peer, _dispycos_var)

        elif _dispycos_req == 'num_jobs':
            if _dispycos_msg.get('auth', None) != _dispycos_auth:
                continue
            _dispycos_var = _dispycos_msg.get('reply_task', None)
            if isinstance(_dispycos_var, pycos.Task):
                _dispycos_var.send(len(_dispycos_job_tasks))

        else:
            logger.warning('invalid command "%s" ignored', _dispycos_req)
            _dispycos_reply_task = _dispycos_msg.get('reply_task', None)
            if not isinstance(_dispycos_reply_task, Task):
                continue
            _dispycos_reply_task.send(-1)

    # kill any pending jobs
    while _dispycos_job_tasks:
        for _dispycos_var in _dispycos_job_tasks:
            _dispycos_var.terminate()
        logger.debug('dispycos server "%s": Waiting for %s tasks to terminate '
                     'before closing client', _dispycos_name, len(_dispycos_job_tasks))
        if (yield _dispycos_jobs_done.wait(timeout=5)):
            break
    if _dispycos_scheduler_task:
        _dispycos_scheduler_task.send({'status': Scheduler.ServerDisconnected,
                                       'location': _dispycos_task.location,
                                       'auth': _dispycos_auth})

    os.chdir(os.path.join(_dispycos_config['dest_path'], '..'))
    try:
        shutil.rmtree(_dispycos_config['dest_path'], ignore_errors=True)
    except Exception:
        pass
    logger.debug('dispycos server %s @ %s done', _dispycos_config['sid'], _dispycos_task.location)
    raise StopIteration({'status': 0, 'restart': _dispycos_restart})


def _dispycos_server_process(_dispycos_mp_queue, _dispycos_config):
    import os
    import sys
    import time
    import signal
    # import traceback

    for _dispycos_var in list(sys.modules.keys()):
        if _dispycos_var.startswith('pycos'):
            sys.modules.pop(_dispycos_var)
    globals().pop('pycos', None)

    global pycos
    import pycos
    import pycos.netpycos
    import pycos.config

    if _dispycos_config['loglevel']:
        pycos.logger.setLevel(pycos.logger.DEBUG)
        # pycos.logger.show_ms(True)
    else:
        pycos.logger.setLevel(pycos.logger.INFO)
    del _dispycos_config['loglevel']

    pycos.logger.name = 'dispycosserver'
    _dispycos_sid = _dispycos_config['sid']
    _dispycos_iid = _dispycos_config['iid']
    _dispycos_auth = _dispycos_config['auth']
    _dispycos_queue, _dispycos_mp_queue = _dispycos_mp_queue, None
    config = {}
    for _dispycos_var in ['udp_port', 'tcp_port', 'node', 'ext_ip_addr', 'name', 'discover_peers',
                          'secret', 'certfile', 'keyfile', 'dest_path', 'max_file_size',
                          'ipv4_udp_multicast']:
        config[_dispycos_var] = _dispycos_config.pop(_dispycos_var, None)

    pycos.config.NetPort = config['udp_port']
    for _dispycos_var in range(5):
        try:
            _dispycos_scheduler = pycos.Pycos(**config)
        except Exception:
            print('dispycos server %s failed for port %s; retrying in 2 seconds' %
                  (_dispycos_sid, config['tcp_port']))
            # print(traceback.format_exc())
            time.sleep(2)
        else:
            break
    else:
        _dispycos_queue.put({'req': 'server_task', 'auth': _dispycos_auth, 'task': None,
                             'iid': _dispycos_iid, 'server_id': _dispycos_sid, 'pid': os.getpid(),
                             'restart': False})
        exit(-1)

    _dispycos_scheduler = pycos.Pycos.instance()

    def prologue(task=None):
        if os.name == 'nt':
            code = _dispycos_config.pop('code')
            if code:
                try:
                    exec(code, globals())
                except Exception:
                    raise StopIteration(-1)
                else:
                    if __name__ == '__mp_main__':  # Windows multiprocessing process
                        sys.modules['__mp_main__'].__dict__.update(globals())
            del code

        loc = pycos.deserialize(_dispycos_config.pop('node_location'))
        yield _dispycos_scheduler.peer(loc)
        node_task = yield pycos.Task.locate('dispycos_node', location=loc, timeout=5)
        if not isinstance(node_task, pycos.Task):
            pycos.logger.warning('%s could not locate node', _dispycos_scheduler.location)
            raise StopIteration(-1)
        _dispycos_config['node_task'] = node_task
        raise StopIteration(0)

    if (pycos.SysTask(prologue).value()) != 0:
        _dispycos_scheduler.ignore_peers = True
        for location in _dispycos_scheduler.peers():
            pycos.Task(_dispycos_scheduler.close_peer, location)
        _dispycos_scheduler.finish()
        _dispycos_queue.put({'req': 'server_task', 'auth': _dispycos_auth, 'task': None,
                             'server_id': _dispycos_sid, 'iid': _dispycos_iid,
                             'pid': os.getpid(), 'status': -1, 'restart': False})
        exit(0)

    _dispycos_node_task = _dispycos_config['node_task']
    _dispycos_task = pycos.SysTask(_dispycos_config.pop('server_proc'))
    _dispycos_config['dest_path'] = config['dest_path']
    _dispycos_task.send(_dispycos_config)
    _dispycos_queue.put({'req': 'server_task', 'auth': _dispycos_auth, 'task': _dispycos_task,
                         'iid': _dispycos_iid, 'server_id': _dispycos_sid, 'pid': os.getpid()})

    def sighandler(signum, frame):
        pycos.logger.debug('Server %s received signal %s', _dispycos_sid, signum)
        if _dispycos_task:
            _dispycos_task.send({'req': 'terminate', 'auth': _dispycos_auth})

    for _dispycos_var in ['SIGINT', 'SIGQUIT', 'SIGHUP', 'SIGTERM']:
        _dispycos_var = getattr(signal, _dispycos_var, None)
        if _dispycos_var:
            signal.signal(_dispycos_var, sighandler)
    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, sighandler)

    _dispycos_config = None
    del config, _dispycos_var, sighandler

    _dispycos_status = _dispycos_task.value()
    _dispycos_task = None
    if not isinstance(_dispycos_status, dict):
        _dispycos_status = {'status': _dispycos_status, 'restart': False}

    def epilogue(task=None):
        _dispycos_scheduler.peer_status(None)
        yield _dispycos_scheduler.peer(_dispycos_node_task.location)
        yield _dispycos_node_task.deliver({'req': 'server_task', 'auth': _dispycos_auth,
                                           'task': None, 'iid': _dispycos_iid,
                                           'server_id': _dispycos_sid}, timeout=5)

    pycos.SysTask(epilogue).value()
    _dispycos_scheduler.ignore_peers = True
    for location in _dispycos_scheduler.peers():
        pycos.Task(_dispycos_scheduler.close_peer, location)
    _dispycos_scheduler.finish()
    _dispycos_queue.put({'req': 'server_task', 'auth': _dispycos_auth, 'task': None,
                         'server_id': _dispycos_sid, 'iid': _dispycos_iid, 'pid': os.getpid(),
                         'status': _dispycos_status.get('status', -1),
                         'restart': _dispycos_status.get('restart', False)})
    exit(0)


def _dispycos_spawn(_dispycos_pipe, _dispycos_config, _dispycos_server_params):
    import os
    import sys
    import signal
    import time
    import threading
    import multiprocessing
    import pickle
    import traceback

    for _dispycos_var in list(globals()):
        if _dispycos_var.startswith('_dispycos_'):
            if _dispycos_var in ('_dispycos_server_process', '_dispycos_server_proc'):
                continue
            globals().pop(_dispycos_var)

    for _dispycos_var in list(sys.modules.keys()):
        if _dispycos_var.startswith('pycos'):
            sys.modules.pop(_dispycos_var)
    globals().pop('pycos', None)

    global pycos
    import pycos

    pycos.logger.name = 'dispycosnode'
    if _dispycos_config['loglevel']:
        pycos.logger.setLevel(pycos.logger.DEBUG)
        # pycos.logger.show_ms(True)
    else:
        pycos.logger.setLevel(pycos.logger.INFO)
    pycos.Pycos.instance()

    class Struct(object):

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __setattr__(self, name, value):
            if hasattr(self, name):
                self.__dict__[name] = value
            else:
                raise AttributeError('Invalid attribute "%s"' % name)

    children = [Struct(sid=sid, port=port, busy_time=busy_time, proc=None, status=None,
                       restart=False, iid=0)
                for sid, port, busy_time in _dispycos_server_params]
    mp_q = multiprocessing.Queue()
    lock = threading.Lock()
    spawn_closed = False
    _dispycos_config['server_proc'] = _dispycos_server_proc
    os.chdir(_dispycos_config['dest_path'])
    sys.path.insert(0, _dispycos_config['dest_path'])
    os.environ['PATH'] = _dispycos_config['dest_path'] + os.pathsep + os.environ['PATH']
    suid = _dispycos_config.pop('suid', None)
    if suid is not None:
        sgid = _dispycos_config.pop('sgid', None)
        if hasattr(os, 'setresuid'):
            os.setresgid(sgid, sgid, sgid)
            os.setresuid(suid, suid, suid)
        else:
            os.setregid(sgid, sgid)
            os.setreuid(suid, suid)

    try:
        with open(os.path.join(_dispycos_config['dest_path'], '..', 'dispycos_client'),
                  'rb') as fd:
            client = pickle.load(fd)
            assert client['auth'] == _dispycos_config['auth']
            setup_args = client['setup_args']
            client = pycos.deserialize(client['client'])
            client._node_allocations = []
            _dispycos_config['server_setup'] = client._server_setup
            _dispycos_config['disable_servers'] = client._disable_servers

        if os.name == 'nt':
            _dispycos_config['code'] = client._code
        else:
            if client._code:
                exec(client._code, globals())

            if client._node_setup:
                try:
                    setup_args = pycos.deserialize(setup_args)
                    ret = pycos.Task(globals()[client._node_setup], *setup_args).value()
                except Exception:
                    pycos.logger.warning('node_setup failed for %s', _dispycos_config['auth'])
                    # print(traceback.format_exc())
                    ret = -1
                assert ret == 0
        client._code = None
        client._node_setup = None
        del setup_args
    except Exception:
        pycos.logger.debug(traceback.format_exc())
        _dispycos_pipe.send({'msg': 'closed', 'auth': _dispycos_config['auth']})
        exit(-1)

    def close_procs(child_procs):
        lock.acquire()
        cur_procs = {child.sid: child.proc if child.status else -1 for child in children}
        lock.release()
        for child in child_procs:
            for j in range(5):
                proc = child.proc
                if not proc or proc != cur_procs[child.sid]:
                    break
                try:
                    if not proc.is_alive():
                        break
                    proc.join(0.4)
                except ValueError:
                    break
            else:
                try:
                    pycos.logger.debug('terminating server %s (%s)', child.sid, proc.pid)
                    if os.name == 'nt':
                        os.kill(proc.pid, signal.CTRL_C_EVENT)
                    else:
                        proc.terminate()
                except Exception:
                    print(traceback.format_exc())
                    pass

        for child in child_procs:
            for j in range(10):
                proc = child.proc
                if not proc or proc != cur_procs[child.sid]:
                    break
                if j == 5:
                    try:
                        pycos.logger.debug('killing server %s (%s)', child.sid, proc.pid)
                        if os.name == 'nt':
                            proc.terminate()
                        else:
                            if hasattr(proc, 'kill'):
                                proc.kill()
                            else:
                                os.kill(proc.pid, signal.SIGKILL)
                    except ValueError:
                        break
                    except Exception:
                        # print(traceback.format_exc())
                        pass
                else:
                    try:
                        if not proc.is_alive():
                            break
                        proc.join(0.4)
                    except ValueError:
                        break

            lock.acquire()
            proc = child.proc
            if proc == cur_procs[child.sid]:
                try:
                    proc.join(1)
                    assert not proc.is_alive()
                    if hasattr(proc, 'close'):
                        try:
                            proc.close()
                        except Exception:
                            pass
                except ValueError:
                    pass
                except Exception:
                    pycos.logger.warning('Could not terminate server %s: %s',
                                         child.sid, child.status)
                    lock.release()
                    continue
                child.status = None
                child.proc = None
                child.busy_time.value = 0
            lock.release()

    def start_proc(child):
        server_config = dict(_dispycos_config)
        server_config['sid'] = child.sid
        server_config['name'] = '%s_server-%s' % (_dispycos_config['name'], server_config['sid'])
        server_config['tcp_port'] = child.port
        server_config['busy_time'] = child.busy_time
        server_config['peers'] = _dispycos_config['peers'][:]
        server_config['dest_path'] = os.path.join(_dispycos_config['dest_path'],
                                                  'dispycos_server_%s' % server_config['sid'])
        lock.acquire()
        if child.status is not None:
            pycos.logger.warning('Server %s already started?: %s', child.sid, child.status)
            lock.release()
            return
        child.iid += 1
        server_config['iid'] = child.iid
        child.status = 'pending'
        proc = multiprocessing.Process(target=_dispycos_server_process, name=server_config['name'],
                                       args=(mp_q, server_config))
        if isinstance(proc, multiprocessing.Process):
            proc.start()
            pycos.logger.debug('dispycos server %s started with PID %s', child.sid, proc.pid)
            child.proc = proc
        else:
            pycos.logger.warning('Could not start dispycos server for %s at %s',
                                 child.sid, child.port)
            child.status = None
        lock.release()

    def pipe_proc():
        for child in children:
            start_proc(child)

        for j in range(60):
            if any(child.status == 'pending' for child in children):
                time.sleep(0.2)
            else:
                break

        _dispycos_pipe.send({'msg': 'started', 'auth': _dispycos_config['auth'],
                             'sids': [child.sid for child in children
                                      if child.proc and child.status == 'running']})

        while 1:
            try:
                req = _dispycos_pipe.recv()
            except Exception:
                break
            if ((not isinstance(req, dict)) or
                (req.get('auth') != _dispycos_config.get('auth'))):
                pycos.logger.warning('Ignoring invalid pipe cmd: %s' % str(req))
                continue

            if req.get('msg') == 'quit':
                client._restart_servers = False
                for child in children:
                    child.restart = False
                mp_q.put({'req': 'quit', 'auth': _dispycos_config['auth']})
                break

            elif req.get('msg') == 'close_server':
                sid = req.get('sid', None)
                if sid:
                    for child in children:
                        if child.sid == sid:
                            break
                    else:
                        pycos.logger.warning('Invalid server id %s', sid)
                        continue
                    child.restart = req.get('restart', False)
                    if child.status is None:
                        if child.restart:
                            try:
                                start_proc(child)
                            except Exception:
                                pycos.logger.warning('Could not restart server %s', sid)
                                pycos.logger.warning(traceback.format_exc())
                    else:
                        if child.status == 'running' and req.get('terminate', False):
                            close_procs([child])
                else:
                    # assert req.get('restart') == False
                    client._restart_servers = req.get('restart', False)
                    if not client._restart_servers:
                        for child in children:
                            child.restart = False
                    _dispycos_pipe.send({'msg': 'restart_ack', 'auth': _dispycos_config['auth']})

            else:
                pycos.logger.warning('spawn: ignoring invalid request')

    pipe_thread = threading.Thread(target=pipe_proc)
    pipe_thread.daemon = True
    pipe_thread.start()

    def sighandler(signum, frame):
        pycos.logger.debug('Spawn received signal %s', signum)
        if spawn_closed:
            pycos.logger.debug('Spawn ignoring signal %s', signum)
            return
        else:
            mp_q.put({'req': 'quit', 'auth': _dispycos_config['auth']})

    for _dispycos_var in ['SIGINT', 'SIGQUIT', 'SIGHUP', 'SIGTERM']:
        _dispycos_var = getattr(signal, _dispycos_var, None)
        if _dispycos_var:
            signal.signal(_dispycos_var, sighandler)
    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, sighandler)

    del _dispycos_var, sighandler

    while 1:
        msg = mp_q.get(block=True)
        if not isinstance(msg, dict):
            pycos.logger.warning('Ignoring mp queue message: %s', type(msg))
            continue
        try:
            auth = msg['auth']
            server_id = msg['server_id']
            iid = msg['iid']
            server_task = msg['task']
        except Exception:
            if msg.get('req') == 'quit' and msg.get('auth') == _dispycos_config['auth']:
                break
            else:
                pycos.logger.debug('Ignoring invalid message')
            continue

        if msg.get('req') != 'server_task':
            pycos.logger.warning('Ignoring invalid server msg %s', msg.get('req'))
            continue
        if auth != _dispycos_config['auth']:
            pycos.logger.warning('Ignoring invalid server msg: %s != %s',
                                 auth, _dispycos_config['auth'])
            continue
        for child in children:
            if child.sid == server_id:
                break
        else:
            pycos.logger.debug('Ignoring server task information for %s', server_id)
            continue
        if child.iid != iid:
            pycos.logger.warning('Invalid instance id for server %s: %s / %s!',
                                 server_id, child.iid, iid)
            continue

        if server_task:
            lock.acquire()
            if child.proc and child.proc.pid == msg.get('pid'):
                if child.status == 'pending':
                    child.status = 'running'
                else:
                    pycos.logger.warning('Invalid status %s for server %s',
                                         child.status, child.sid)
            else:
                pycos.logger.warning('Invalid PID for server %s: %s', server_id, msg.get('pid'))
                child.status = 'running'
            lock.release()
        else:
            # assert server_task is None
            proc = child.proc
            try:
                if proc and proc.pid == msg['pid'] or server.status == 'running':
                    close_procs([child])
                else:
                    pycos.logger.warning('Ignoring invalid server message %s', msg)
            except ValueError:
                pass
            except Exception:
                print(traceback.format_exc())
                continue

            if (msg.get('status', -1) == 0 and
                (child.restart or client._restart_servers or msg.get('restart', False))):
                start_proc(child)
                if child.restart:
                    child.restart = False

    spawn_closed = True
    client._restart_servers = False
    for child in children:
        child.restart = False
    for child in children:
        if child.proc:
            try:
                child.proc.join(2)
            except Exception:
                pass
    close_procs(children)
    mp_q.close()
    _dispycos_pipe.send({'msg': 'closed', 'auth': _dispycos_config['auth']})
    exit(0)


def _dispycos_node():
    if ((hasattr(os, 'setresuid') or hasattr(os, 'setreuid')) and os.getuid() != os.geteuid()):
        _dispycos_config['suid'] = os.geteuid()
        _dispycos_config['sgid'] = os.getegid()
        if _dispycos_config['suid'] == 0:
            print('\n    WARNING: Python interpreter %s likely has suid set to 0 '
                  '\n    (administrator privilege), which is dangerous.\n\n' %
                  sys.executable)
        if _dispycos_config['sgid'] == 0:
            print('\n    WARNING: Python interpreter %s likely has sgid set to 0 '
                  '\n    (administrator privilege), which is dangerous.\n\n' %
                  sys.executable)

        os.setegid(os.getgid())
        os.seteuid(os.getuid())
        os.umask(0x007)
        pycos.logger.info('Clients will run with uid %s and gid %s' %
                          (_dispycos_config['suid'], _dispycos_config['sgid']))
    else:
        _dispycos_config.pop('suid', None)
        _dispycos_config.pop('sgid', None)

    if not _dispycos_config['min_pulse_interval']:
        _dispycos_config['min_pulse_interval'] = MinPulseInterval
    if not _dispycos_config['max_pulse_interval']:
        _dispycos_config['max_pulse_interval'] = MaxPulseInterval
    if _dispycos_config['msg_timeout'] < 1:
        raise Exception('msg_timeout must be at least 1')
    if (_dispycos_config['min_pulse_interval'] and
        _dispycos_config['min_pulse_interval'] < _dispycos_config['msg_timeout']):
        raise Exception('min_pulse_interval must be at least msg_timeout')
    if (_dispycos_config['max_pulse_interval'] and _dispycos_config['min_pulse_interval'] and
        _dispycos_config['max_pulse_interval'] < _dispycos_config['min_pulse_interval']):
        raise Exception('max_pulse_interval must be at least min_pulse_interval')
    if _dispycos_config['zombie_period']:
        if _dispycos_config['zombie_period'] < _dispycos_config['min_pulse_interval']:
            raise Exception('zombie_period must be at least min_pulse_interval')
    else:
        _dispycos_config['zombie_period'] = 0

    num_cpus = multiprocessing.cpu_count()
    if _dispycos_config['cpus'] > 0:
        if _dispycos_config['cpus'] > num_cpus:
            raise Exception('CPU count must be <= %s' % num_cpus)
        num_cpus = _dispycos_config['cpus']
    elif _dispycos_config['cpus'] < 0:
        if -_dispycos_config['cpus'] >= num_cpus:
            raise Exception('CPU count must be > -%s' % num_cpus)
        num_cpus += _dispycos_config['cpus']
    del _dispycos_config['cpus']

    node_ports = set()
    for node_port in _dispycos_config.pop('node_ports', []):
        node_port = node_port.split('-')
        if len(node_port) == 1:
            node_ports.add(int(node_port[0]))
        elif len(node_port) == 2:
            node_port = (int(node_port[0]), min(int(node_port[1]),
                                                int(node_port[0]) + num_cpus - len(node_ports)))
            node_ports = node_ports.union(range(node_port[0], node_port[1] + 1))
        else:
            raise Exception('Invalid TCP port range "%s"' % str(node_port))

    if node_ports:
        node_ports = sorted(node_ports)
        node_ports = node_ports[:num_cpus + 1]
    else:
        node_ports = [eval(pycos.config.DispycosNodePort)]

    for node_port in range(node_ports[-1] + 1, node_ports[-1] + 1 + num_cpus - len(node_ports) + 1):
        if node_ports[-1]:
            node_ports.append(node_port)
        else:
            node_ports.append(0)
    del node_port
    _dispycos_config['udp_port'] = node_ports[0]

    peer = None
    for _dispycos_id in range(len(_dispycos_config['peers'])):
        peer = _dispycos_config['peers'][_dispycos_id].rsplit(':', 1)
        if len(peer) != 2:
            print('peer "%s" is not valid' % _dispycos_config['peers'][_dispycos_id])
            exit(1)
        try:
            peer = pycos.Location(peer[0], peer[1])
        except Exception:
            print('peer "%s" is not valid' % _dispycos_config['peers'][_dispycos_id])
            exit(1)
        _dispycos_config['peers'][_dispycos_id] = pycos.serialize(peer)
    del peer

    node_name = _dispycos_config['name']
    if not node_name:
        node_name = socket.gethostname()
        if not node_name:
            node_name = 'dispycos_server'

    daemon = _dispycos_config.pop('daemon', False)
    if not daemon:
        try:
            if os.getpgrp() != os.tcgetpgrp(sys.stdin.fileno()):
                daemon = True
        except Exception:
            pass
        if os.name == 'nt':
            # Python 3 under Windows blocks multiprocessing.Process on reading
            # input; pressing "Enter" twice works (for one subprocess). Until
            # this is understood / fixed, disable reading input.

            # As of Sep 28, 2018, it seems multiprocessing works with
            # reading input, but don't what fixed it, so for now, leave it as is.
            # As of Aug 1, 2020, all seem okay with Python 3.8 and pywin32 228.
            # However, older versions seem to now work fine with multiprocessing, but
            # signal SIGBREAK is raised when a client quits!
            print('\n  In the past, reading input in non-daemon mode seemed to\n'
                  '  interfere with multiprocessing. Latest Python / pywin32\n'
                  '  seem to be working fine. In case of issues, use "--daemon" option.\n')

    _dispycos_config['discover_peers'] = False

    class Struct(object):

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __setattr__(self, name, value):
            if hasattr(self, name):
                self.__dict__[name] = value
            else:
                raise AttributeError('Invalid attribute "%s"' % name)

    service_times = Struct(start=None, stop=None, end=None)
    # time at start of day
    _dispycos_var = time.localtime()
    _dispycos_var = (int(time.time()) - (_dispycos_var.tm_hour * 3600) -
                     (_dispycos_var.tm_min * 60) - _dispycos_var.tm_sec)
    if _dispycos_config['service_start']:
        service_times.start = time.strptime(_dispycos_config.pop('service_start'), '%H:%M')
        service_times.start = (_dispycos_var + (service_times.start.tm_hour * 3600) +
                               (service_times.start.tm_min * 60))
    if _dispycos_config['service_stop']:
        service_times.stop = time.strptime(_dispycos_config.pop('service_stop'), '%H:%M')
        service_times.stop = (_dispycos_var + (service_times.stop.tm_hour * 3600) +
                              (service_times.stop.tm_min * 60))
    if _dispycos_config['service_end']:
        service_times.end = time.strptime(_dispycos_config.pop('service_end'), '%H:%M')
        service_times.end = (_dispycos_var + (service_times.end.tm_hour * 3600) +
                             (service_times.end.tm_min * 60))

    if (service_times.start or service_times.stop or service_times.end):
        if not service_times.start:
            service_times.start = int(time.time())
        if service_times.stop:
            if service_times.start >= service_times.stop:
                raise Exception('"service_start" must be before "service_stop"')
        if service_times.end:
            if service_times.start >= service_times.end:
                raise Exception('"service_start" must be before "service_end"')
            if service_times.stop and service_times.stop >= service_times.end:
                raise Exception('"service_stop" must be before "service_end"')
        if not service_times.stop and not service_times.end:
            raise Exception('"service_stop" or "service_end" must also be given')

    if _dispycos_config['max_file_size']:
        _dispycos_var = re.match(r'(\d+)([kKmMgGtT]?)', _dispycos_config['max_file_size'])
        if (not _dispycos_var or
            len(_dispycos_var.group(0)) != len(_dispycos_config['max_file_size'])):
            raise Exception('Invalid max_file_size option')
        _dispycos_config['max_file_size'] = int(_dispycos_var.group(1))
        _dispycos_var = _dispycos_var.group(2)
        if _dispycos_var:
            _dispycos_config['max_file_size'] *= 1024**({'k': 1, 'm': 2, 'g': 3,
                                                         't': 4}[_dispycos_var.lower()])
    else:
        _dispycos_config['max_file_size'] = 0

    if _dispycos_config['certfile']:
        _dispycos_config['certfile'] = os.path.abspath(_dispycos_config['certfile'])
    else:
        _dispycos_config['certfile'] = None
    if _dispycos_config['keyfile']:
        _dispycos_config['keyfile'] = os.path.abspath(_dispycos_config['keyfile'])
    else:
        _dispycos_config['keyfile'] = None

    node_auth = hashlib.sha1(os.urandom(20)).hexdigest()
    node_servers = [None] * (num_cpus + 1)
    if _dispycos_config['dest_path']:
        dispycos_path = _dispycos_config['dest_path']
    else:
        import tempfile
        dispycos_path = os.path.join(os.sep, tempfile.gettempdir(), 'pycos')
    dispycos_path = os.path.join(dispycos_path, 'dispycos', 'node')
    _dispycos_config['dest_path'] = dispycos_path

    if not os.path.isdir(dispycos_path):
        try:
            os.makedirs(dispycos_path)
        except Exception:
            print('Could not create directory "%s"' % dispycos_path)
            exit(1)

    if os.name == 'nt':
        # if sys.version_info.major == 3 and sys.version_info.minor < 8 and not daemon:
        proc_signals = [signal.CTRL_C_EVENT, signal.CTRL_C_EVENT, signal.SIGTERM]
    else:
        proc_signals = [signal.SIGINT, 0, signal.SIGKILL]

    def kill_proc(pid, ppid, kill):
        if pid <= 0:
            return 0
        pycos.logger.info('Killing process with PID %s', pid)
        psproc = None
        if psutil:
            try:
                psproc = psutil.Process(pid)
                assert psproc.is_running()
                if psproc.ppid() not in [ppid, 1]:
                    pycos.logger.warning('PPID of PID %s is different from expected %s: %s',
                                         pid, ppid, psproc.ppid())
                    return -1
                assert any(arg.endswith('dispycosnode.py') for arg in psproc.cmdline())
                psproc.send_signal(proc_signals[0])
            except psutil.NoSuchProcess:
                return 0
            except Exception:
                pycos.logger.debug(traceback.format_exc())
                return -1

        if not psproc:
            try:
                os.kill(pid, proc_signals[0])
            except ProcessLookupError:
                return 0
            except Exception:
                # TODO: handle failures
                pycos.logger.debug(traceback.format_exc())

        for signum in range(1, len(proc_signals) if kill else (len(proc_signals) - 1)):
            for i in range(20):
                if psproc:
                    try:
                        psproc.wait(0.2)
                    except Exception:
                        pass
                    if not psproc.is_running():
                        return 0
                    if i == 15:
                        try:
                            if signum == 1:
                                psproc.terminate()
                            else:
                                psproc.kill()
                        except psutil.NoSuchProcess:
                            return 0
                        except Exception:
                            pycos.logger.debug(traceback.format_exc())
                else:
                    time.sleep(0.2)
                    if proc_signals[signum] == 0 or i == 15:
                        try:
                            os.kill(pid, proc_signals[signum])
                        except OSError:
                            return 0
        pycos.logger.debug('Could not terminate PID %s', pid)
        return -1

    for _dispycos_id in range(0, num_cpus + 1):
        _dispycos_var = os.path.join(dispycos_path, '..', 'server-%d.pkl' % _dispycos_id)
        node_servers[_dispycos_id] = Struct(
            id=_dispycos_id, iid=1, task=None, name='%s_server-%s' % (node_name, _dispycos_id),
            port=node_ports[_dispycos_id], restart=False, pid_file=_dispycos_var, done=pycos.Event(),
            busy_time=multiprocessing.Value('I', 0) if _dispycos_id > 0 else None
        )
    node_servers[0].name = None

    comp_state = Struct(auth=None, scheduler=None, client_location=None, cpus_reserved=0,
                        spawn_mpproc=None, interval=_dispycos_config['max_pulse_interval'],
                        abandon_zombie=False, served=0)

    if _dispycos_config['clean']:
        if os.path.isfile(node_servers[0].pid_file):
            with open(node_servers[0].pid_file, 'rb') as fd:
                pid_info = pickle.load(fd)
                if kill_proc(pid_info['pid'], pid_info['ppid'], kill=False):
                    for _dispycos_var in range(20):
                        if not os.path.isfile(node_servers[0].pid_file):
                            break
                        time.sleep(0.2)
                    else:
                        kill_proc(pid_info['spid'], pid_info['pid'], kill=True)
                        kill_proc(pid_info['pid'], pid_info['ppid'], kill=True)
                else:
                    if os.path.isfile(node_servers[0].pid_file):
                        try:
                            os.remove(node_servers[0].pid_file)
                        except Exception:
                            pass

    if os.path.exists(node_servers[0].pid_file):
        print('\n    Another dispycosnode seems to be running;\n'
              '    ensure no dispycosnode and servers are running and\n'
              '    remove *.pkl files in %s"\n' % (os.path.join(dispycos_path, '..')))
        exit(1)

    dispycos_pid = os.getpid()
    if hasattr(os, 'getppid'):
        dispycos_ppid = os.getppid()
    else:
        dispycos_ppid = 1
    with open(node_servers[0].pid_file, 'wb') as fd:
        # TODO: store and check crate_time with psutil
        pickle.dump({'pid': dispycos_pid, 'ppid': dispycos_ppid, 'spid': -1}, fd)

    server_config = {}
    for _dispycos_var in ['udp_port', 'tcp_port', 'node', 'ext_ip_addr', 'name',
                          'discover_peers', 'secret', 'certfile', 'keyfile', 'dest_path',
                          'max_file_size', 'ipv4_udp_multicast']:
        server_config[_dispycos_var] = _dispycos_config.get(_dispycos_var, None)
    server_config['name'] = '%s_dispycosnode' % node_name
    server_config['tcp_port'] = node_ports[0]
    dispycos_scheduler = pycos.Pycos(**server_config)
    dispycos_scheduler.ignore_peers = True
    for _dispycos_var in dispycos_scheduler.peers():
        pycos.Task(dispycos_scheduler.close_peer, _dispycos_var)
    if dispycos_path != dispycos_scheduler.dest_path:
        print('\n    Destination paths inconsistent: "%s" != "%s"\n' %
              (dispycos_path, dispycos_scheduler.dest_path))
        exit(1)
    if 'suid' in _dispycos_config:
        os.chown(dispycos_path, -1, _dispycos_config['sgid'])
        os.chmod(dispycos_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                 stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_ISGID)
    os.chmod(os.path.join(dispycos_path, '..'), stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
             stat.S_IXGRP)
    os.chdir(dispycos_path)

    del _dispycos_id
    parent_pipe, child_pipe = multiprocessing.Pipe(duplex=True)

    def node_proc(task=None):
        from pycos.dispycos import DispycosNodeAvailInfo, DispycosNodeInfo

        task.register('dispycos_node')
        ping_interval = _dispycos_config.pop('ping_interval')
        msg_timeout = _dispycos_config['msg_timeout']
        zombie_period = _dispycos_config['zombie_period']
        disk_path = dispycos_scheduler.dest_path
        _dispycos_config['node_location'] = pycos.serialize(task.location)

        def close_server(server, terminate=False, restart=False, task=None):
            parent_pipe.send({'msg': 'close_server', 'auth': comp_state.auth,
                              'sid': server.id, 'terminate': False, 'restart': restart})
            if not server.task:
                raise StopIteration
            if (yield server.task.deliver({'req': 'terminate' if terminate else 'quit',
                                           'auth': comp_state.auth})) != 1:
                parent_pipe.send({'msg': 'close_server', 'auth': comp_state.auth,
                                  'sid': server.id, 'terminate': bool(terminate),
                                  'restart': False})
                server.task = None
                server.done.set()

        def service_available():
            now = time.time()
            if not _dispycos_config['serve']:
                return False
            if not service_times.start:
                return True
            if service_times.stop:
                if (service_times.start <= now < service_times.stop):
                    return True
            else:
                if (service_times.start <= now < service_times.end):
                    return True
            return False

        def service_times_proc(task=None):
            task.set_daemon()
            while 1:
                now = int(time.time())
                yield task.sleep(service_times.start - now)
                dispycos_scheduler.ignore_peers = False
                dispycos_scheduler.discover_peers(port=pycos.config.NetPort)

                if service_times.stop:
                    now = int(time.time())
                    yield task.sleep(service_times.stop - now)
                    for server in node_servers:
                        if server.task:
                            pycos.Task(close_server, server)

                dispycos_scheduler.ignore_peers = True
                if service_times.end:
                    now = int(time.time())
                    yield task.sleep(service_times.end - now)
                    for server in node_servers:
                        if server.task:
                            pycos.Task(close_server, server, terminate=True)

                    for peer in dispycos_scheduler.peers():
                        pycos.Task(dispycos_scheduler.close_peer, peer)

                # advance times for next day
                service_times.start += 24 * 3600
                if service_times.stop:
                    service_times.stop += 24 * 3600
                if service_times.end:
                    service_times.end += 24 * 3600

        def monitor_peers(task=None):
            task.set_daemon()
            while 1:
                msg = yield task.receive()
                if not isinstance(msg, pycos.PeerStatus):
                    continue
                if msg.status == pycos.PeerStatus.Offline:
                    if (comp_state.scheduler and comp_state.scheduler.location == msg.location):
                        node_task.send({'req': 'release', 'auth': comp_state.auth})
                else:  # msg.status == pycos.PeerStatus.Online
                    if comp_state.scheduler and comp_state.scheduler.location == msg.location:
                        node_task.send({'req': 'release', 'auth': comp_state.auth})

        def server_task_msg(msg):
            try:
                auth = msg['auth']
                server_id = msg['server_id']
                iid = msg['iid']
                server_task = msg['task']
            except Exception:
                return -1

            if auth != comp_state.auth and comp_state.auth:
                pycos.logger.warning('Ignoring invalid server msg %s: %s != %s',
                                     auth, comp_state.auth)
                return -1
            if server_id < 1 or server_id > len(node_servers):
                pycos.logger.debug('Ignoring server task information for %s', server_id)
                return -1
            server = node_servers[server_id]
            if server_task:
                if not isinstance(server_task, pycos.SysTask):
                    pycos.logger.warning('Invalid task: %s', type(server_task))
                    return -1
                if server.task and server.task != server_task:
                    if iid != server.iid:
                        pycos.logger.warning('Updating server %s instance ID from %s to %s',
                                             server_id, server.iid, iid)
                if not comp_state.cpus_reserved:
                    server_task.send({'req': 'terminate', 'auth': comp_state.auth})
                    return 0
                server.task = server_task
                server.iid = iid
                server.done.clear()
                server_task.send({'auth': comp_state.auth, 'node_task': node_task})
                pid = msg.get('pid', None)
                with open(server.pid_file, 'wb') as fd:
                    pickle.dump({'pid': pid, 'ppid': comp_state.spawn_mpproc.pid}, fd)
                return 0
            else:
                if server.iid != iid:
                    pycos.logger.warning('Invalid server %s instance ID %s / %s!',
                                         server_id, server.iid, iid)
                server.task = None
                server.done.set()
                server.iid += 1
                if os.path.exists(server.pid_file):
                    try:
                        os.remove(server.pid_file)
                    except Exception:
                        pass
                return 0

        def start_client():
            # clear pipe
            while parent_pipe.poll(0.1):
                parent_pipe.recv()
            while child_pipe.poll(0.1):
                child_pipe.recv()
            if comp_state.spawn_mpproc:
                try:
                    if not comp_state.spawn_mpproc.is_alive():
                        comp_state.spawn_mpproc.join(1)
                        if hasattr(comp_state.spawn_mpproc, 'close'):
                            comp_state.spawn_mpproc.close()
                except Exception:
                    pass
                comp_state.spawn_mpproc = None
            _dispycos_config['scheduler_location'] = pycos.serialize(comp_state.scheduler.location)
            _dispycos_config['client_location'] = pycos.serialize(comp_state.client_location)
            _dispycos_config['auth'] = comp_state.auth
            if comp_state.interval < _dispycos_config['min_pulse_interval']:
                comp_state.interval = _dispycos_config['min_pulse_interval']
                pycos.logger.warning('Pulse interval for client has been raised to %s',
                                     comp_state.interval)
            if zombie_period:
                _dispycos_config['pulse_interval'] = min(comp_state.interval,
                                                         zombie_period / 3)
            else:
                _dispycos_config['pulse_interval'] = comp_state.interval

            servers = [server for server in node_servers if server.id and not server.task]
            servers = servers[:comp_state.cpus_reserved]
            if not servers:
                return 0
            for server in servers:
                server.busy_time.value = int(time.time())
            args = (child_pipe, _dispycos_config,
                    [(server.id, server.port, server.busy_time) for server in servers])
            comp_state.spawn_mpproc = multiprocessing.Process(target=_dispycos_spawn, args=args)
            comp_state.spawn_mpproc.start()
            with open(node_servers[0].pid_file, 'wb') as fd:
                pickle.dump({'pid': dispycos_pid, 'ppid': dispycos_ppid,
                             'spid': comp_state.spawn_mpproc.pid}, fd)
            if parent_pipe.poll(30):
                msg = parent_pipe.recv()
                if (isinstance(msg, dict) and msg.get('msg', None) == 'started' and
                    msg.get('auth', None) == comp_state.auth):
                    for i in msg['sids']:
                        if i < 1 or i >= len(node_servers):
                            pycos.logger.warning('Invalid server ID: %s', i)
                            # quit?
                            continue
                        server = node_servers[i]
                        server.done.clear()
                        server.iid = 1
                    cpus = len(msg['sids'])
                else:
                    cpus = 0
            else:
                cpus = 0
            if not cpus:
                comp_state.spawn_mpproc = None
            return cpus

        def close_client(req='close', restart=False, task=None):
            if comp_state.cpus_reserved and comp_state.spawn_mpproc:
                parent_pipe.send({'msg': 'close_server', 'auth': comp_state.auth, 'sid': 0,
                                  'restart': False})
                if parent_pipe.poll(2):
                    msg = parent_pipe.recv()
                    if (isinstance(msg, dict) and msg.get('auth', None) == comp_state.auth and
                        msg.get('msg') == 'restart_ack'):
                        pass
            for server in node_servers:
                if server.task:
                    pycos.Task(close_server, server, terminate=(req == 'terminate'))
            if not comp_state.cpus_reserved:
                raise StopIteration
            cpus_reserved, comp_state.cpus_reserved = comp_state.cpus_reserved, 0
            cur_auth = comp_state.auth
            for server in node_servers:
                if server.task:
                    yield server.done.wait()
            if comp_state.spawn_mpproc and comp_state.spawn_mpproc.is_alive():
                proc = comp_state.spawn_mpproc
                parent_pipe.send({'msg': 'quit', 'auth': comp_state.auth})
                for i in range(5):
                    if parent_pipe.poll(2):
                        if comp_state.auth != cur_auth:
                            raise StopIteration
                        msg = parent_pipe.recv()
                        if (isinstance(msg, dict) and msg.get('msg', None) == 'closed' and
                            msg.get('auth', None) == comp_state.auth):
                            proc.join(2)
                            if proc == comp_state.spawn_mpproc and not proc.is_alive():
                                if hasattr(proc, 'close'):
                                    try:
                                        proc.close()
                                    except Exception:
                                        pass
                                comp_state.spawn_mpproc = None
                            break
                else:
                    if comp_state.auth != cur_auth:
                        raise StopIteration
                    if proc == comp_state.spawn_mpproc and proc.is_alive():
                        try:
                            if os.name == 'nt':
                                os.kill(proc.pid, proc_signals[0])
                            else:
                                proc.terminate()
                        except Exception:
                            pass
                        proc.join(2)
                        if comp_state.auth != cur_auth:
                            raise StopIteration
                        for i in range(10):
                            if proc == comp_state.spawn_mpproc and not proc.is_alive():
                                break
                            proc.join(2)
                            if comp_state.auth != cur_auth:
                                raise StopIteration
                            if i == 9:
                                try:
                                    if os.name == 'nt':
                                        proc.terminate()
                                    else:
                                        if hasattr(proc, 'kill'):
                                            proc.kill()
                                        else:
                                            os.kill(proc.pid, proc_signals[2])
                                except ProcessLookupError:
                                    break
                                except OSError:
                                    break
                                except Exception:
                                    pass
                    if proc == comp_state.spawn_mpproc:
                        proc.join(2)
                        if proc == comp_state.spawn_mpproc and not proc.is_alive():
                            if hasattr(proc, 'close'):
                                try:
                                    proc.close()
                                except Exception:
                                    pass
                            comp_state.spawn_mpproc = None

                # clear pipe
                while parent_pipe.poll(0.1):
                    parent_pipe.recv()
                while child_pipe.poll(0.1):
                    child_pipe.recv()
            if comp_state.auth != cur_auth:
                raise StopIteration

            for server in node_servers:
                if not server.id:
                    continue
                for i in range(20):
                    if server.task:
                        yield task.sleep(0.2)
                    else:
                        if comp_state.auth != cur_auth:
                            raise StopIteration
                        path = os.path.join(dispycos_path, 'dispycos_server_%s' % server.id)
                        if os.path.isdir(path):
                            try:
                                shutil.rmtree(path)
                            except Exception:
                                pycos.logger.warning('Could not remove "%s"', path)
                        break
            if comp_state.auth != cur_auth:
                raise StopIteration
            if restart:
                comp_state.cpus_reserved = cpus_reserved
                if start_client() == 0:
                    yield close_client(req='close', restart=False, task=task)
                raise StopIteration
            if os.path.isdir(dispycos_path):
                for name in os.listdir(dispycos_path):
                    name = os.path.join(dispycos_path, name)
                    try:
                        if os.path.isfile(name):
                            os.remove(name)
                        else:
                            shutil.rmtree(name, ignore_errors=True)
                    except Exception:
                        pycos.logger.warning('Could not remove "%s"' % name)

            f = os.path.join(dispycos_path, '..', 'dispycos_client')
            try:
                os.remove(f)
            except Exception:
                pycos.logger.warning('Client to remove "%s" vanished!', f)
            pycos.Task(dispycos_scheduler.close_peer, comp_state.client_location)
            if not os.path.isdir(dispycos_path):
                pycos.logger.warning('Apparently dispycosnode directory "%s" vanished after '
                                     'client from %s', dispycos_path, comp_state.scheduler)
                try:
                    os.path.makedirs(dispycos_path)
                    if 'suid' in _dispycos_config:
                        os.chmod(dispycos_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                                 | stat.S_IXGRP | stat.S_IXOTH)
                except Exception:
                    pycos.logger.warning('Could not create dispyconode directory "%s"',
                                         dispycos_path)
                    _dispycos_config['serve'] = 0

            comp_state.scheduler.send({'status': pycos.dispycos.Scheduler.NodeClosed,
                                       'location': node_task.location, 'auth': comp_state.auth})
            comp_state.auth = None
            comp_state.interval = _dispycos_config['max_pulse_interval']
            comp_state.scheduler = None
            comp_state.client_location = None
            comp_state.served += 1
            timer_task.resume()
            if req == 'quit' or req == 'terminate':
                _dispycos_config['serve'] = 0
            elif _dispycos_config['serve'] > 0:
                _dispycos_config['serve'] -= 1

            if _dispycos_config['serve']:
                if service_available():
                    dispycos_scheduler.ignore_peers = False
                    dispycos_scheduler.discover_peers(port=pycos.config.NetPort)
            else:
                if all(not server.task for server in node_servers):
                    parent_pipe.close()
                    child_pipe.close()
                node_task.send({'req': 'exit', 'auth': node_auth})

            raise StopIteration

        def timer_proc(task=None):
            task.set_daemon()
            last_pulse = last_ping = time.time()
            while 1:
                yield task.sleep(comp_state.interval)
                now = time.time()
                if comp_state.scheduler:
                    msg = {'status': 'pulse', 'location': task.location}
                    if psutil:
                        msg['node_status'] = DispycosNodeAvailInfo(
                            task.location, 100.0 - psutil.cpu_percent(),
                            psutil.virtual_memory().available, psutil.disk_usage(disk_path).free,
                            100.0 - psutil.swap_memory().percent)

                    sent = yield comp_state.scheduler.deliver(msg, timeout=msg_timeout)
                    if sent == 1:
                        last_pulse = now
                    elif comp_state.scheduler and (now - last_pulse) > (5 * comp_state.interval):
                        pycos.logger.warning('Scheduler is not reachable; closing client "%s"',
                                             comp_state.auth)
                        node_task.send({'req': 'close', 'auth': node_auth})
                        pycos.Task(dispycos_scheduler.close_peer, comp_state.scheduler.location)

                    if (zombie_period and comp_state.scheduler):
                        if (comp_state.abandon_zombie and
                            (all(server.task and (now - server.busy_time.value) > zombie_period)
                             for server in node_servers)):
                            pycos.logger.debug('Closing zombie computation %s from %s',
                                               comp_state.comp_auth, comp_state.client_location)
                            node_task.send({'req': 'close', 'auth': node_auth})
                        else:
                            zombie_servers = [
                                server for server in node_servers if
                                (server.task and (now - server.busy_time.value) > zombie_period)]
                            for server in zombie_servers:
                                if (server.busy_time.value and
                                    ((now - server.busy_time.value) < (2 * zombie_period))):
                                    pycos.logger.debug('server %s inactive!', server.id)
                                    pycos.Task(close_server, server)
                                else:
                                    pycos.logger.debug('server %s died!', server.id)
                                    pycos.Task(close_server, server, terminate=True)

                if ping_interval and (now - last_ping) > ping_interval and service_available():
                    dispycos_scheduler.discover_peers(port=pycos.config.NetPort)
                    last_ping = now

        timer_task = pycos.Task(timer_proc)
        dispycos_scheduler.peer_status(pycos.Task(monitor_peers))
        if service_times.start:
            pycos.Task(service_times_proc)
        else:
            dispycos_scheduler.ignore_peers = False
            dispycos_scheduler.discover_peers(port=pycos.config.NetPort)

        for peer in _dispycos_config['peers']:
            pycos.Task(dispycos_scheduler.peer, pycos.deserialize(peer))

        while 1:
            msg = yield task.receive()
            try:
                req = msg['req']
            except Exception:
                continue

            if req == 'server_task':
                server_task_msg(msg)

            elif req == 'dispycos_node_info':
                reply_task = msg.get('reply_task', None)
                if isinstance(reply_task, pycos.Task):
                    if psutil:
                        info = DispycosNodeAvailInfo(task.location,
                                                     100.0 - psutil.cpu_percent(),
                                                     psutil.virtual_memory().available,
                                                     psutil.disk_usage(disk_path).free,
                                                     100.0 - psutil.swap_memory().percent)
                    else:
                        info = DispycosNodeAvailInfo(task.location, None, None, None, None)
                    info = DispycosNodeInfo(node_name, task.location.addr,
                                            len(node_servers) - 1, platform.platform(), info)
                    reply_task.send(info)

            elif req == 'reserve':
                # TODO: if current client is zombie and has abandon_zombie set, release it
                # and accept new reservation
                reply_task = msg.get('reply_task', None)
                cpus = msg.get('cpus', -1)
                auth = msg.get('auth', None)
                if (isinstance(reply_task, pycos.Task) and isinstance(cpus, int) and
                    isinstance(msg.get('status_task', None), pycos.Task) and auth and
                    isinstance(msg.get('client_location', None), pycos.Location)):
                    pass
                else:
                    continue

                avail_cpus = len([server for server in node_servers
                                  if server.id and not server.task])
                if (not comp_state.scheduler) and service_available() and (0 < cpus <= avail_cpus):
                    if (yield dispycos_scheduler.peer(msg['client_location'])):
                        cpus = 0
                    else:
                        if not cpus:
                            cpus = avail_cpus
                else:
                    cpus = 0

                if cpus:
                    auth = hashlib.sha1(os.urandom(20)).hexdigest()
                else:
                    auth = None
                comp_state.interval = msg['pulse_interval']
                comp_state.abandon_zombie = msg.get('abandon_zombie', False)
                if comp_state.interval < _dispycos_config['min_pulse_interval']:
                    comp_state.interval = _dispycos_config['min_pulse_interval']
                    pycos.logger.warning('Pulse interval for client from %s has been '
                                         'raised to %s', reply_task.location, comp_state.interval)
                if zombie_period:
                    _dispycos_config['pulse_interval'] = min(comp_state.interval,
                                                             zombie_period / 3)
                else:
                    _dispycos_config['pulse_interval'] = comp_state.interval
                # TODO: inform scheduler about pulse interval
                info = {'cpus': cpus, 'auth': auth}
                if ((yield reply_task.deliver(info, timeout=msg_timeout)) == 1 and cpus):
                    comp_state.auth = auth
                    comp_state.cpus_reserved = cpus
                    comp_state.scheduler = msg['status_task']
                    comp_state.client_location = msg['client_location']
                    dispycos_scheduler.ignore_peers = True
                    timer_task.resume()
                else:
                    dispycos_scheduler.ignore_peers = False
                    dispycos_scheduler.discover_peers(port=pycos.config.NetPort)

            elif req == 'client':
                reply_task = msg.get('reply_task', None)
                client = msg.get('client', None)
                if (comp_state.auth == msg.get('auth', None) and
                    isinstance(reply_task, pycos.Task) and comp_state.cpus_reserved > 0):
                    with open(os.path.join(dispycos_path, '..', 'dispycos_client'), 'wb') as fd:
                        pickle.dump({'auth': comp_state.auth, 'client': client,
                                     'setup_args': msg['setup_args']}, fd)
                    cpus = start_client()
                    if ((yield reply_task.deliver(cpus)) == 1) and cpus:
                        comp_state.cpus_reserved = cpus
                        timer_task.resume()
                    else:
                        pycos.Task(close_client)
                del client

            elif req == 'release':
                auth = msg.get('auth', None)
                if comp_state.auth == auth:
                    setup_args = msg.get('setup_args', None)
                    if setup_args:
                        client = None
                        try:
                            with open(os.path.join(dispycos_path, '..', 'dispycos_client'),
                                      'rb') as fd:
                                client = pickle.load(fd)
                            client['setup_args'] = setup_args
                            with open(os.path.join(dispycos_path, '..', 'dispycos_client'),
                                      'wb') as fd:
                                pickle.dump(client, fd)
                        except Exception:
                            pycos.logger.warning('Could not save setup_args for client from %s',
                                                 comp_state.scheduler.location)
                        del client
                    restart = msg.get('restart', False)
                    if msg.get('terminate', False):
                        req = 'terminate'
                    else:
                        req = 'close'
                    pycos.Task(close_client, req, restart=restart)

            elif req == 'close' or req == 'quit' or req == 'terminate':
                auth = msg.get('auth', None)
                if auth == node_auth:
                    if _dispycos_config['serve']:
                        if req != 'close':
                            _dispycos_config['serve'] = 0
                    elif req == 'quit':
                        req = 'terminate'
                    if comp_state.scheduler:
                        pycos.Task(close_client, req=req)
                    elif req == 'quit' or req == 'terminate':
                        break

            elif req == 'exit':
                auth = msg.get('auth', None)
                if auth == node_auth:
                    break

            elif req == 'status':
                if (msg.get('status_task') == comp_state.scheduler and
                    msg.get('auth') == comp_state.auth):
                    info = {'auth': comp_state.auth,
                            'servers': [server.task for server in node_servers
                                        if server.id and server.task]}
                else:
                    info = None
                reply_task = msg.get('reply_task', None)
                if isinstance(reply_task, pycos.Task):
                    reply_task.send(info)

            elif req == 'close_server':
                auth = msg.get('auth', None)
                loc = msg.get('addr', None)
                if (loc and comp_state.auth and auth == comp_state.auth):
                    for server in node_servers:
                        # TODO: make sure this server is reserved?
                        if server.port == loc.port:
                            break
                    else:
                        server = None
                    if server:
                        if server.task:
                            pycos.Task(close_server, server, terminate=msg.get('terminate', False),
                                       restart=msg.get('restart', False))

            elif req == 'abandon_zombie':
                auth = msg.get('auth', None)
                if auth == comp_state.auth:
                    comp_state.abandon_zombie = msg.get('flag', False)

            else:
                pycos.logger.warning('Invalid message %s ignored',
                                     str(msg) if isinstance(msg, dict) else '')

        if os.name == 'nt':
            os.kill(dispycos_pid, signal.CTRL_C_EVENT)
        else:
            os.kill(dispycos_pid, signal.SIGINT)

    _dispycos_config['name'] = node_name
    _dispycos_config['dest_path'] = dispycos_path
    node_task = pycos.Task(node_proc)

    def sighandler(signum, frame):
        pycos.logger.debug('dispycosnode (%s) received signal %s', dispycos_pid, signum)
        if comp_state.spawn_mpproc:
            try:
                parent_pipe.send({'msg': 'quit', 'auth': comp_state.auth})
            except Exception:
                pass
        if node_task.is_alive():
            node_task.send({'req': 'quit', 'auth': node_auth})
        else:
            raise KeyboardInterrupt

    for _dispycos_var in ['SIGINT', 'SIGQUIT', 'SIGHUP', 'SIGTERM']:
        _dispycos_var = getattr(signal, _dispycos_var, None)
        if _dispycos_var:
            signal.signal(_dispycos_var, sighandler)
    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, sighandler)

    del server_config, node_ports, _dispycos_var, sighandler

    if daemon:
        while 1:
            try:
                time.sleep(3600)
            except Exception:
                if node_task.is_alive():
                    node_task.send({'req': 'quit', 'auth': node_auth})
                break
    else:
        while 1:
            try:
                # wait a bit for any output for previous command is done
                time.sleep(0.2)
                cmd = input(
                        '\nEnter\n'
                        '  "status" to get status\n'
                        '  "close" to stop accepting new jobs and\n'
                        '          close current client when current jobs are finished\n'
                        '  "quit" to "close" current client and exit dispycosnode\n'
                        '  "terminate" to kill current jobs and "quit": ')
            except (KeyboardInterrupt, EOFError):
                if node_task.is_alive():
                    node_task.send({'req': 'quit', 'auth': node_auth})
                break
            else:
                cmd = cmd.strip().lower()
                if not cmd:
                    cmd = 'status'

            print('')
            if cmd == 'status':
                print('  %s clients served so far' % comp_state.served)
                for server in node_servers:
                    if server.task:
                        server.task.send({'req': cmd, 'auth': comp_state.auth})
                    elif server.id:
                        print('  dispycos server "%s" is not currently used' % server.name)
            elif cmd in ('close', 'quit', 'terminate'):
                if not node_task.is_alive():
                    break
                node_task.send({'req': cmd, 'auth': node_auth})

    try:
        node_task.value()
    except (Exception, KeyboardInterrupt):
        pass
    for peer in dispycos_scheduler.peers():
        pycos.Task(dispycos_scheduler.close_peer, peer)

    if os.path.isfile(node_servers[0].pid_file):
        try:
            os.remove(node_servers[0].pid_file)
        except Exception:
            pycos.logger.debug(traceback.format_exc())
            pass
    # if 'suid' in _dispycos_config:
    #     os.setegid(_dispycos_config['sgid'])
    #     os.seteuid(_dispycos_config['suid'])
    exit(0)


if __name__ == '__main__':

    """
    See http://pycos.sourceforge.io/dispycos.html#node-servers for details on
    options to start this program.
    """

    import sys
    import time
    import argparse
    import multiprocessing
    import socket
    import os
    import stat
    import hashlib
    import re
    import signal
    import platform
    import shutil
    import pickle
    import traceback
    try:
        import readline
    except Exception:
        pass
    try:
        import psutil
    except ImportError:
        print('\n'
              '    "psutil" module is not available;\n'
              '    wihout it, using "clean" option may be dangerous and\n'
              '    CPU, memory, disk status will not be sent to scheduler / client!\n')
        psutil = None
    else:
        psutil.cpu_percent(0.1)

    import pycos
    import pycos.netpycos
    import pycos.config
    from pycos.dispycos import MinPulseInterval, MaxPulseInterval

    pycos.logger.name = 'dispycosnode'
    # PyPI / pip packaging adjusts assertion below for Python 3.7+
    assert sys.version_info.major == 3 and sys.version_info.minor < 7, \
        ('"%s" is not suitable for Python version %s.%s; use file installed by pip instead' %
         (__file__, sys.version_info.major, sys.version_info.minor))

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', dest='config', default='',
                        help='use configuration in given file')
    parser.add_argument('--save_config', dest='save_config', default='',
                        help='save configuration in given file and exit')
    parser.add_argument('-c', '--cpus', dest='cpus', type=int, default=0,
                        help='number of CPUs/dispycos instances to run; '
                        'if negative, that many CPUs are not used')
    parser.add_argument('-i', '--ip_addr', dest='node', action='append', default=[],
                        help='IP address or host name of this node')
    parser.add_argument('--ext_ip_addr', dest='ext_ip_addr', action='append', default=[],
                        help='External IP address to use (needed in case of NAT firewall/gateway)')
    parser.add_argument('--scheduler_port', dest='scheduler_port', type=str,
                        default=eval(pycos.config.DispycosSchedulerPort),
                        help='port number for dispycos scheduler')
    parser.add_argument('--node_ports', dest='node_ports', action='append', default=[],
                        help='port numbers for dispycos node')
    parser.add_argument('--ipv4_udp_multicast', dest='ipv4_udp_multicast', action='store_true',
                        default=False, help='use multicast for IPv4 UDP instead of broadcast')
    parser.add_argument('-n', '--name', dest='name', default='',
                        help='(symbolic) name given to Pycos schdulers on this node')
    parser.add_argument('--dest_path', dest='dest_path', default='',
                        help='path prefix to where files sent by peers are stored')
    parser.add_argument('--max_file_size', dest='max_file_size', default='',
                        help='maximum file size of any file transferred')
    parser.add_argument('-s', '--secret', dest='secret', default='',
                        help='authentication secret for handshake with peers')
    parser.add_argument('--certfile', dest='certfile', default='',
                        help='file containing SSL certificate')
    parser.add_argument('--keyfile', dest='keyfile', default='',
                        help='file containing SSL key')
    parser.add_argument('--serve', dest='serve', default=-1, type=int,
                        help='number of clients to serve before exiting')
    parser.add_argument('--service_start', dest='service_start', default='',
                        help='time of day in HH:MM format when to start service')
    parser.add_argument('--service_stop', dest='service_stop', default='',
                        help='time of day in HH:MM format when to stop service '
                        '(continue to execute running jobs, but no new jobs scheduled)')
    parser.add_argument('--service_end', dest='service_end', default='',
                        help='time of day in HH:MM format when to end service '
                        '(terminate running jobs)')
    parser.add_argument('--msg_timeout', dest='msg_timeout', default=pycos.config.MsgTimeout,
                        type=int, help='timeout for delivering messages')
    parser.add_argument('--min_pulse_interval', dest='min_pulse_interval',
                        default=MinPulseInterval, type=int,
                        help='minimum pulse interval clients can use in number of seconds')
    parser.add_argument('--max_pulse_interval', dest='max_pulse_interval',
                        default=MaxPulseInterval, type=int,
                        help='maximum pulse interval clients can use in number of seconds')
    parser.add_argument('--zombie_period', dest='zombie_period', default=(10 * MaxPulseInterval),
                        type=int,
                        help='maximum number of seconds for client to not run tasks')
    parser.add_argument('--ping_interval', dest='ping_interval', default=0, type=int,
                        help='interval in number of seconds for node to broadcast its address')
    parser.add_argument('--daemon', action='store_true', dest='daemon', default=False,
                        help='if given, input is not read from terminal')
    parser.add_argument('--clean', action='store_true', dest='clean', default=False,
                        help='if given, server processes from previous run will be killed '
                        'and new server process started')
    parser.add_argument('--peer', dest='peers', action='append', default=[],
                        help='peer location (in the form node:TCPport) to communicate')
    parser.add_argument('-d', '--debug', action='store_true', dest='loglevel', default=False,
                        help='if given, debug messages are printed')
    _dispycos_config = vars(parser.parse_args(sys.argv[1:]))

    if _dispycos_config['clean'] and not psutil:
        print('\n    Using "clean" option without "psutil" module is dangerous!\n')

    _dispycos_var = _dispycos_config.pop('config')
    if _dispycos_var:
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(_dispycos_var)
        cfg = dict(cfg.items('DEFAULT'))
        cfg['cpus'] = int(cfg['cpus'])
        cfg['port'] = int(cfg['port'])
        cfg['serve'] = int(cfg['serve'])
        cfg['msg_timeout'] = int(cfg['msg_timeout'])
        cfg['min_pulse_interval'] = int(cfg['min_pulse_interval'])
        cfg['max_pulse_interval'] = int(cfg['max_pulse_interval'])
        cfg['zombie_period'] = int(cfg['zombie_period'])
        cfg['ping_interval'] = int(cfg['ping_interval'])
        cfg['daemon'] = cfg['daemon'] == 'True'
        cfg['clean'] = cfg['clean'] == 'True'
        # cfg['discover_peers'] = cfg['discover_peers'] == 'True'
        cfg['loglevel'] = cfg['loglevel'] == 'True'
        cfg['node_ports'] = [_dispycos_var.strip()[1:-1] for _dispycos_var in
                             cfg['node_ports'][1:-1].split(',')]
        cfg['node_ports'] = [_dispycos_var for _dispycos_var in cfg['node_ports'] if _dispycos_var]
        cfg['ipv4_udp_multicast'] = cfg['ipv4_udp_multicast'] == 'True'
        cfg['peers'] = [_dispycos_var.strip()[1:-1] for _dispycos_var in
                        cfg['peers'][1:-1].split(',')]
        cfg['peers'] = [_dispycos_var for _dispycos_var in cfg['peers'] if _dispycos_var]
        for key, value in _dispycos_config.items():
            if _dispycos_config[key] != parser.get_default(key) or key not in cfg:
                cfg[key] = _dispycos_config[key]
        _dispycos_config = cfg
        del key, value, cfg

    _dispycos_var = _dispycos_config.pop('save_config')
    if _dispycos_var:
        import configparser
        cfg = configparser.ConfigParser(_dispycos_config)
        cfgfp = open(_dispycos_var, 'w')
        cfg.write(cfgfp)
        cfgfp.close()
        exit(0)

    del parser, sys.modules['argparse'], globals()['argparse'], _dispycos_var
    pycos.config.DispycosSchedulerPort = int(_dispycos_config.pop('scheduler_port'))

    if _dispycos_config['loglevel']:
        pycos.logger.setLevel(pycos.Logger.DEBUG)
    else:
        pycos.logger.setLevel(pycos.Logger.INFO)
    _dispycos_node()
