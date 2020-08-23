# Run 'dispycosnode.py' program on one or more nodes (to start servers to execute computations
# sent by this client), and this program on local computer.

# this is a simple example of multi-agent system (with a few caveats)

import pycos.netpycos as pycos
from pycos.dispycos import *


# this generator function is sent to remote dispycos servers to run tasks (agents) there
def agent_proc(channel, task=None):
    import random

    # register this task so server peers can discover each other
    task.register()

    # find peers (other agents); it is not required in this example, but useful / required in
    # real world multi-agent systems
    agents = set()

    def peer_status(task=None):
        task.set_daemon()
        while 1:
            status = yield task.receive()
            if isinstance(status, pycos.PeerStatus):
                if status.status == pycos.PeerStatus.Online:
                    pycos.logger.debug('%s: found peer %s', task.location, status.location)
                    # get reference to task (with same name) at that location
                    agent = pycos.Task.locate(task.name, timeout=3)
                    if isinstance(agent, pycos.Task):
                        agents.add(agent)
                else:
                    pycos.logger.debug('%s: peer %s disconnected', task.location, status.location)
                    for agent in agents:
                        if agent.location == status.location:
                            agent.discard(agent)
                            break

    pycos_scheduler = pycos.Pycos.instance()
    # peer_status gets notifications of peers online and offline
    pycos_scheduler.peer_status(pycos.Task(peer_status))
    # broadcast discover message for discovery; firewall must allow UDP on port 9706; if
    # necessary, this can be done periodically
    pycos_scheduler.discover_peers()

    # channel running at client, as done here, is not good for multi-agent system, as its failure
    # can break agents' ability to communicate; in this example channel is sometimes used to show
    # latest updates to user (as well as to illustrate use cases for channels).
    yield channel.subscribe(task)
    # in this simple case agents discover low and high valeues (silly exercise) randomly by
    # cooperating to accelerate the process
    low = high = random.uniform(1, 10000)
    while 1:
        # computation is simulated with waiting
        msg = yield task.recv(timeout=random.uniform(2, 6))
        if msg:
            low = msg[1]
            high = msg[2]
            continue
        n = random.uniform(1, 10000)
        if n < low:
            low = n
        elif n > high:
            high = n
        else:
            continue

        if random.random() < 0.5:
            channel.send((task, low, high))
        elif agents:
            # This agent can send to each known agent (in 'agents' captured with 'peer_status') if
            # required (without using channel); here data is sent to one random agent
            agent = random.choice(agents)
            agent.send((task, low, high))


# status messages indicating nodes, servers and remote tasks finish status are sent to this local
# task; in this case we process only servers initialized and closed
def status_proc(task=None):
    task.set_daemon()
    while 1:
        msg = yield task.receive()
        if isinstance(msg, DispycosStatus):
            if msg.status == Scheduler.ServerInitialized:
                # start new agent at this server
                agent = yield computation.run_at(msg.info, agent_proc, channel)
                # there is no need to keep track of agents in this example, but done so here to
                # show potential use
                if isinstance(agent, pycos.Task):
                    agents.add(agent)
            elif msg.status == Scheduler.ServerClosed or msg.status == Scheduler.ServerAbandoned:
                for agent in agents:
                    if agent.location == msg.info:
                        agents.discard(agent)
                        break


# this local task submits client to dispycos scheduler, shows latest updates on channel
def client_proc(computation, task=None):
    # schedule computation with the scheduler
    if (yield computation.schedule()):
        raise Exception('schedule failed')

    yield channel.subscribe(task)
    while 1:
        msg = yield task.recv()
        if not msg:  # from main to terminate
            break
        # from channel with latest low / high values
        pycos.logger.info('Update from %s: %.3f / %.3f ', msg[0].location, msg[1], msg[2])

    yield computation.close(terminate=True)


if __name__ == '__main__':
    import pycos.dispycos, sys, re
    pycos.logger.setLevel(pycos.Logger.DEBUG)
    # if scheduler is not already running (on a node as a program), start it (private scheduler):
    Scheduler()
    kwargs = {
        'status_task': pycos.Task(status_proc),
        'restart_servers': True,
        }
    computation = Computation([agent_proc], **kwargs)
    agents = set()  # for illustration - not required in this example
    channel = pycos.Channel('update_channel')
    client_task = pycos.Task(client_proc, computation)
    print('   Enter "quit" or "exit" to end the program, or ')
    print('   Enter one or two numbers separated by "," to reset low and high values')
    if sys.version_info.major > 2:
        read_input = input
    else:
        read_input = raw_input

    servers = set()

    while True:
        try:
            inp = read_input().strip().lower()
            if inp == 'quit' or inp == 'exit':
                break
            else:
                try:
                    m = re.match(r'\s*(\d+)[,/\s]*(\d*)', inp)
                    low = float(m.group(1))
                    if m.group(2):
                        high = float(m.group(2))
                    else:
                        high = low
                    inp = (low, high)
                    # reset low and high for servers with given data (this is not in the spirit of
                    # mulit-agent system behavior, but for illustration)
                    channel.send((client_task, low, high))
                except Exception:
                    print('Invalid command "%s" ignored' % inp)
        except KeyboardInterrupt:
            break
    client_task.send(None)
