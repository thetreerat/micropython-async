try:
    import utime as time
except ImportError:
    import time
import utimeq
import logging


DEBUG = 0

log = logging.getLogger("asyncio")

type_gen = type((lambda: (yield))())

class EventLoop:

    def __init__(self, len=42):
        # lpqlen encoded in len to avoid modifying __init__.py
        # I'm lazy and want to maintain one file only.
        lpqlen, qlen = divmod(len, 100000)
        self.q = utimeq.utimeq(qlen)
        self.lpq = utimeq.utimeq(lpqlen)

    def time(self):
        return time.ticks_ms()

    def create_task(self, coro):
        # CPython 3.4.2
        self.call_later_ms_(0, coro)
        # CPython asyncio incompatibility: we don't return Task object

    def call_lp_(self, callback, args=()):
        # low priority. time is only for debug here and in run_forever
        time = self.time()
        if __debug__ and DEBUG:
            log.debug("Scheduling LP %s", (time, callback, args))
        self.lpq.push(time, callback, args)

    def call_lp(self, callback, *args):
        # low priority. time is only for debug here and in run_forever
        time = self.time()
        if __debug__ and DEBUG:
            log.debug("Scheduling LP %s", (time, callback, args))
        self.lpq.push(time, callback, args)

    def call_soon(self, callback, *args):
        self.call_at(self.time(), callback, *args)

    def call_later(self, delay, callback, *args):
        self.call_at(time.ticks_add(self.time(), int(delay * 1000)), callback, *args)

    def call_later_ms_(self, delay, callback, args=()):
        self.call_at_(time.ticks_add(self.time(), delay), callback, args)

    def call_at(self, time, callback, *args):
        if __debug__ and DEBUG:
            log.debug("Scheduling %s", (time, callback, args))
        self.q.push(time, callback, args)

    def call_at_(self, time, callback, args=()):
        if __debug__ and DEBUG:
            log.debug("Scheduling %s", (time, callback, args))
        self.q.push(time, callback, args)

    def wait(self, delay):
        # Default wait implementation, to be overriden in subclasses
        # with IO scheduling
        if __debug__ and DEBUG:
            log.debug("Sleeping for: %s", delay)
        time.sleep_ms(delay)

    def run_forever(self):
        cur_task = [0, 0, 0]
        while True:
            if self.q:
                # wait() may finish prematurely due to I/O completion,
                # and schedule new, earlier than before tasks to run.
                while 1:
                    t = self.q.peektime()
                    tnow = self.time()
                    delay = time.ticks_diff(t, tnow)
                    if delay <= 0:
                        self.q.pop(cur_task)
                        break
                    if self.lpq:
                        self.lpq.pop(cur_task)
                        break
                    self.wait(delay)

                t = cur_task[0]
                cb = cur_task[1]
                args = cur_task[2]
                if __debug__ and DEBUG:
                    log.debug("Next coroutine to run: %s", (t, cb, args))
#                __main__.mem_info()
            else:
                if self.lpq:
                    self.lpq.pop(cur_task)
                    cb = cur_task[1]
                    args = cur_task[2]
                else:
                    self.wait(-1)
                    # Assuming IO completion scheduled some tasks
                    continue
            if callable(cb):
                cb(*args)
            else:
                delay = 0
                try:
                    if __debug__ and DEBUG:
                        log.debug("Coroutine %s send args: %s", cb, args)
                    if args == ():
                        ret = next(cb)
                    else:
                        ret = cb.send(*args)
                    if __debug__ and DEBUG:
                        log.debug("Coroutine %s yield result: %s", cb, ret)
                    if isinstance(ret, SysCall1):
                        arg = ret.arg
                        if isinstance(ret, Sleep):
                            delay = int(arg * 1000)
                        if isinstance(ret, SleepMs):
                            delay = arg
                        elif isinstance(ret, IORead):
#                            self.add_reader(ret.obj.fileno(), lambda self, c, f: self.call_soon(c, f), self, cb, ret.obj)
#                            self.add_reader(ret.obj.fileno(), lambda c, f: self.call_soon(c, f), cb, ret.obj)
#                            self.add_reader(arg.fileno(), lambda cb: self.call_soon(cb), cb)
                            self.add_reader(arg, cb)
                            continue
                        elif isinstance(ret, IOWrite):
#                            self.add_writer(arg.fileno(), lambda cb: self.call_soon(cb), cb)
                            self.add_writer(arg, cb)
                            continue
                        elif isinstance(ret, IOReadDone):
                            self.remove_reader(arg)
                        elif isinstance(ret, IOWriteDone):
                            self.remove_writer(arg)
                        elif isinstance(ret, StopLoop):
                            return arg
                        else:
                            assert False, "Unknown syscall yielded: %r (of type %r)" % (ret, type(ret))
                    elif isinstance(ret, type_gen):
                        self.call_soon(ret)
                    elif isinstance(ret, int):
                        # Delay
                        delay = ret
                    elif ret is low_priority:
                        self.call_lp_(cb, args)
                        continue
                    elif ret is None:
                        # Just reschedule
                        pass
                    else:
                        assert False, "Unsupported coroutine yield value: %r (of type %r)" % (ret, type(ret))
                except StopIteration as e:
                    if __debug__ and DEBUG:
                        log.debug("Coroutine finished: %s", cb)
                    continue
                self.call_later_ms_(delay, cb, args)

    def run_until_complete(self, coro):
        def _run_and_stop():
            yield from coro
            yield StopLoop(0)
        self.call_soon(_run_and_stop())
        self.run_forever()

    def close(self):
        pass


class SysCall:

    def __init__(self, *args):
        self.args = args

    def handle(self):
        raise NotImplementedError

# Optimized syscall with 1 arg
class SysCall1(SysCall):

    def __init__(self, arg):
        self.arg = arg

class Sleep(SysCall1):
    pass

class StopLoop(SysCall1):
    pass

class IORead(SysCall1):
    pass

class IOWrite(SysCall1):
    pass

class IOReadDone(SysCall1):
    pass

class IOWriteDone(SysCall1):
    pass

class LowPriority():
    def __iter__(self):
        yield self

# Singleton awaitable
low_priority = LowPriority()

_event_loop = None
_event_loop_class = EventLoop
def get_event_loop(qlen=42, lpqlen=42):
    global _event_loop
    if _event_loop is None:
        _event_loop = _event_loop_class(qlen + 1000000 * lpqlen)
    return _event_loop

def sleep(secs):
    yield int(secs * 1000)

# Implementation of sleep_ms awaitable with zero heap memory usage
class SleepMs(SysCall1):

    def __init__(self):
        self.v = None
        self.arg = None

    def __call__(self, arg):
        self.v = arg
        #print("__call__")
        return self

    def __iter__(self):
        #print("__iter__")
        return self

    def __next__(self):
        if self.v is not None:
            #print("__next__ syscall enter")
            self.arg = self.v
            self.v = None
            return self
        #print("__next__ syscall exit")
        _stop_iter.__traceback__ = None
        raise _stop_iter

_stop_iter = StopIteration()
sleep_ms = SleepMs()


def coroutine(f):
    return f

#
# The functions below are deprecated in uasyncio, and provided only
# for compatibility with CPython asyncio
#

def ensure_future(coro, loop=_event_loop):
    _event_loop.call_soon(coro)
    # CPython asyncio incompatibility: we don't return Task object
    return coro


# CPython asyncio incompatibility: Task is a function, not a class (for efficiency)
def Task(coro, loop=_event_loop):
    # Same as async()
    _event_loop.call_soon(coro)
