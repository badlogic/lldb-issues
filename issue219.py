import commands
import os
import platform
import sys
import threading
import time

try: 
    # Just try for LLDB in case PYTHONPATH is already correctly setup
    import lldb
except ImportError:
    lldb_python_dirs = list()
    # lldb is not in the PYTHONPATH, try some defaults for the current platform
    platform_system = platform.system()
    if platform_system == 'Darwin':
        # local debug build of lldb, must set LLDB_DEBUGSERVER_PATH env var for this to work
        lldb_python_dirs.append("/Users/badlogic/workspaces/robovm/robovm-debug/lldb/debug/macosx-x86_64/llvm/lib/python2.7/site-packages")
        
        # On Darwin, try the currently selected Xcode directory
        xcode_dir = commands.getoutput("xcode-select --print-path")
        if xcode_dir:
            lldb_python_dirs.append(os.path.realpath(xcode_dir + '/../SharedFrameworks/LLDB.framework/Resources/Python'))
            lldb_python_dirs.append(xcode_dir + '/Library/PrivateFrameworks/LLDB.framework/Resources/Python')
        lldb_python_dirs.append('/System/Library/PrivateFrameworks/LLDB.framework/Resources/Python')
    success = False
    for lldb_python_dir in lldb_python_dirs:
        if os.path.exists(lldb_python_dir):
            if not (sys.path.__contains__(lldb_python_dir)):
                sys.path.append(lldb_python_dir)
                try: 
                    import lldb
                except ImportError:
                    pass
                else:
                    print 'imported lldb from: "%s"' % (lldb_python_dir)
                    success = True
                    break
    if not success:
        print "error: couldn't locate the 'lldb' module, please set PYTHONPATH correctly"
        sys.exit(1)
            
def log(tag, message):
    print "%s: %s" % (tag, message)
            
class Task():
    def __init__(self, callback, name):
        self.callback = callback
        self.name = name
        self.exception = None
        self.latch = threading.Event()
        self.value = None
        
    def run(self, eventProcessor):
        try:
            self.value = self.callback(eventProcessor)
        except Exception as e:
            self.exception = e
        finally:
            self.latch.set()
    
    def wait(self, timeOut):
        if not self.latch.wait(timeOut):
            raise Exception("Timed out waiting for task %s" % self.name)        
        else:
            if self.exception != None:
                raise Exception
            else:
                return self.value

class EventListener():
    def running(self, event):
        return
    def stopped(self, event):
        return
    def exited(self, event):
        return
        
class SymbolBreakPointListener(EventListener):
    def __init__(self, eventProcessor, symbol):                
        self.eventProcessor = eventProcessor
        self.symbol = symbol
        self.latch = threading.Event()
        eventProcessor.addListener(self)        
    
    def stopped(self, event):
        for thread in self.eventProcessor.process:
            if thread.GetStopReason() == lldb.eStopReasonBreakpoint:
                frame = thread.GetFrameAtIndex(0)
                if self.symbol == frame.GetFunctionName():
                    self.latch.set()
                    
    def wait(self, timeOut):
        try:
            if not self.latch.wait(timeOut):
                raise Exception("Timed out waiting for breakpoint at symbol %s" % self.symbol)
        finally:
            self.eventProcessor.removeListener(self)
            
class OneTimeStateListener(EventListener):
    def __init__(self, eventProcessor, state):
       self.eventProcessor = eventProcessor
       self.state = state 
       self.latch = threading.Event()
       eventProcessor.addListener(self)
    
    def running(self, event):
        if lldb.SBProcess.GetStateFromEvent(event) == self.state:
            self.latch.set()
            
    def stopped(self, event):
        if lldb.SBProcess.GetStateFromEvent(event) == self.state:
            self.latch.set()
    
    def exited(self, event):
        if lldb.SBProcess.GetStateFromEvent(event) == self.state:
            self.latch.set()  
    
    def wait(self, timeOut):
        try:
            if not self.latch.wait(timeOut):
                raise Exception("Timed out waiting for state %s" % self.state)
        finally:
            self.eventProcessor.removeListener(self)
            
class ThreadListener(EventListener):
    def __init__(self, eventProcessor):
        self.eventProcessor = eventProcessor            
        eventProcessor.addListener(self)
        setSymbolBreakpoint(eventProcessor.target, "threadStart")
        setSymbolBreakpoint(eventProcessor.target, "threadEnd")                
    
    def stopped(self, event):
        tag = "ThreadListener.stopped"
        stoppedInHook = False
        otherStopReason = False
        
        for thread in self.eventProcessor.process:
            if thread.GetStopReason() == lldb.eStopReasonBreakpoint:
                if thread.GetFrameAtIndex(0).GetFunctionName() == "threadStart":                   
                    log(tag, "Thread started: %s" % thread.GetFrameAtIndex(0).GetValueForVariablePath("thread"))
                    stoppedInHook = True
                elif thread.GetFrameAtIndex(0).GetFunctionName() == "threadEnd":
                    log(tag, "Thread ended: %s" % thread.GetFrameAtIndex(0).GetValueForVariablePath("thread"))
                    stoppedInHook = True
                else:
                    otherStopReason = True
            elif thread.GetStopReason() != lldb.eStopReasonNone:
                otherStopReason = True
        
        if stoppedInHook:
            if not otherStopReason:
                log(tag, "Resuming after hook")
                self.eventProcessor.resumeVotes = self.eventProcessor.resumeVotes + 1
            else:
                log(tag, "Would resume, but non-hook stop reason found")                                
        
class EventProcessor(threading.Thread):
    def __init__(self, debugger, target, process, listener):
        threading.Thread.__init__(self)
        self.debugger = debugger
        self.target = target
        self.process = process
        self.listener = listener
        self.task = None
        self.running = True
        self.lastState = None
        self.listeners = []
        self.lock = threading.RLock()      
        self.requestedStopForTask = False
        self.suspendVotes = 0
        self.resumeVotes = 0
        return
    
    def addListener(self, listener):
        with self.lock:
            self.listeners.append(listener)
            
    def removeListener(self, listener):
        with self.lock:
            self.listeners.remove(listener)
    
    def postTask(self, task, name, timeOut):
        tag = "EventProcessor.postTask"
        with self.lock:
            log(tag, "Submitted task %s to event processor thread" % name)
            self.task = Task(task, name)
        self.task.wait(timeOut)
    
    def run(self):
        tag = "EventProcessor.run"
        log(tag, "Started event processor thread")
        while self.running:
            # get the next event, may be None
            event = self.pollEvent()
                         
            # if we didn't get an event, and if no task
            # is pending or a ask is waiting for suspension
            with self.lock:   
                if event == None:
                    if self.task == None:
                        continue
                    else:
                        if(self.requestedStopForTask):
                            continue
                                                   
            
            # broadcast the event to all listeners registered via addListener()
            self.broadcastEvent(event)
            
            # check if we have a task and execute it if we are in a stopped state
            # otherwise interrupt the process and wait for it to stop   
            self.executeTask()                        
            
            # check suspend/resume votes and act accordingly
            self.checkVotes(event)
            
            # exit if the process is dead
            if self.process.GetState() == lldb.eStateCrashed or \
               self.process.GetState() == lldb.eStateDetached or \
               self.process.GetState() == lldb.eStateExited:
                break;
            
            # sleep a tiny bit to yield CPU            
            # time.sleep(0.002)
            
            # delimit this iteration            
            log(tag, "======== Finished event loop iteration ======\n\n")                    
        
        self.running = False;            
        
        
    def pollEvent(self):
        tag = "EventProcessor.pollEvent"
        event = lldb.SBEvent()
        if self.listener.PeekAtNextEvent(event):
            self.listener.WaitForEvent(1, event)
            if event.IsValid() and lldb.SBProcess.EventIsProcessEvent(event):
                log(tag, "Got process event")
                return event           
        
        return None
    
    def executeTask(self):
        tag = "EventProcessor.executeTask"
        with self.lock:
            if self.task != None:
                if self.process.GetState() == lldb.eStateStopped:
                    log(tag, "Executing task %s" % self.task.name)
                    self.task.run(self)
                    self.task = None
                    if self.requestedStopForTask:
                        self.resumeVotes = self.resumeVotes + 1
                        self.requestedStopForTask = False
                        log(tag, "Voting for process resume after task execution")
                elif not self.requestedStopForTask:
                    log(tag, "Requesting process suspension for task %s" % self.task.name)
                    self.suspendVotes = self.suspendVotes + 1
                    self.requestedStopForTask = True
                else:
                    log(tag, "Requested process suspension, waiting for stop event")
            else:
                log(tag, "No task to execute")
        return
    
    def checkVotes(self, event):
        tag = "EventProcessor.checkVotes"
        
        # don't do anything if we got no votes or no event
        if self.suspendVotes == 0 and self.resumeVotes == 0:
            log(tag, "No votes")
            return
        
        log(tag, "Vote results: suspend=%s, resume=%s" % (self.suspendVotes, self.resumeVotes))
        if self.suspendVotes > self.resumeVotes and self.process.GetState() == lldb.eStateRunning:
            error = self.process.Stop()
            if not error.IsValid() or error.Fail():
                log(tag, "Failed to stop process, reason: %s" % error.GetCString())
            else:
                log(tag, "Stopped process")
        elif self.suspendVotes < self.resumeVotes and self.process.GetState() != lldb.eStateRunning:
            error = self.process.Continue()
            if not error.IsValid() or error.Fail():
                log(tag, "Failed to resume process, reason: %s" % error.GetCString())
            else:
                log(tag, "Resumed process")
        else:
            log(tag, "Vote tie or suspends > resumes in stopped state or resumes > suspends in running state, not doing anything")
        self.suspendVotes = 0
        self.resumeVotes = 0
        return
           
    def broadcastEvent(self, event):
        tag = "EventProcessor.broadcastEvent"        
        if event != None and event.IsValid():
            state = lldb.SBProcess.GetStateFromEvent(event)            
            if state == lldb.eStateAttaching:
                log(tag, "Attaching")
            elif state == lldb.eStateConnected:
                log(tag, "Connecting")
            elif state == lldb.eStateCrashed:
                log(tag, "Crashed")
                with self.lock:
                    for l in self.listeners:
                        l.exited(event)
            elif state == lldb.eStateDetached:
                log(tag, "Detached")
                with self.lock:
                    for l in self.listeners:
                        l.exited(event)
            elif state == lldb.eStateExited:
                log(tag, "Exited")
                with self.lock:
                    for l in self.listeners:
                        l.exited(event)
            elif state == lldb.eStateInvalid:
                log(tag, "Invalid")
            elif state == lldb.eStateLaunching:
                log(tag, "Launching")
            elif state == lldb.eStateRunning:            
                log(tag, "Running")    
                if(self.lastState != lldb.eStateRunning):                    
                    with self.lock:
                        for l in self.listeners:
                            l.running(event)                        
            elif state == lldb.eStateStepping:
                log(tag, "Stepping")
            elif state == lldb.eStateSuspended:
                log(tag, "Suspended")
            elif state == lldb.eStateStopped:
                log(tag, "Stopped")
                self.logThreads()
                with self.lock:
                    for l in self.listeners:
                        if not l.stopped(event):
                            break
            elif state == lldb.eStateUnloaded:
                log(tag, "Unloaded")
            else:
                log(tag, "Unknown state: %d" % state)   
            self.lastState = state            
        else: 
            log(tag, "No event to broadcast")      
    
    def logThreads(self):
        for thread in self.process:
            reason = thread.GetStopReason()
            if(reason != lldb.eStopReasonNone):
                print "==> Stopped thread #%s, %s, reason: %s" % (thread.GetIndexID(), thread.GetFrameAtIndex(0), self.stopReasonToString(reason))
                
    def stopReasonToString(self, stopReason):
        if stopReason == lldb.eStopReasonInvalid:
            return "eStopReasonInvalid"
        elif stopReason == lldb.eStopReasonNone:
            return "eStopReasonNone"
        elif stopReason == lldb.eStopReasonTrace:
            return "eStopReasonTrace"
        elif stopReason == lldb.eStopReasonBreakpoint:
            return "eStopReasonBreakpoint"
        elif stopReason == lldb.eStopReasonWatchpoint:
            return "eStopReasonWatchpoint"
        elif stopReason == lldb.eStopReasonSignal:
            return "eStopReasonSignal"
        elif stopReason == lldb.eStopReasonException:
            return "eStopReasonException"
        elif stopReason == lldb.eStopReasonExec:
            return "eStopReasonExec"
        elif stopReason == lldb.eStopReasonPlanComplete:
            return "eStopReasonPlanComplete"
        elif stopReason == lldb.eStopReasonThreadExiting:
            return "eStopReasonThreadExiting"
        elif stopReaon == lldb.eStopReasonInstrumentation:
            return "eStopReasonInstrumentation"
        else:
            return "Unknown stop reason"        
        
    def terminate(self):
        self.running = False
        self.join()
        self.process.Kill()
        self.process.Destroy()
        self.debugger.DeleteTarget(self.target)        

def launch(argv):
    if len(argv) != 1:        
        raise Exception("Usage: issue291 <executable>")
     
    lldb.SBDebugger.Initialize()
    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync (True)
    
    target = debugger.CreateTarget(argv[0])
    if not target.IsValid():
        print "Invalid target"
        return
    
    # stop in main
    setSymbolBreakpoint(target, "main")
    
    launchInfo = lldb.SBLaunchInfo([])
    error = lldb.SBError()
    process = target.Launch(launchInfo, error)
    if error.Fail():
        print("Couldn't launch %s, error: %s" % (argv[0], error.GetCString()))
    
    listener = debugger.GetListener()
    eventProcessor = EventProcessor(debugger, target, process, listener)
    eventProcessor.start()
        
    # wait until we stopped in main
    bp = SymbolBreakPointListener(eventProcessor, "main")
    bp.wait(5)
    return eventProcessor

def setSymbolBreakpoint(target, symbol):
    bp = target.BreakpointCreateByName(symbol)
    if not bp.IsValid() or bp.GetNumLocations() < 1:
        raise Exception("Couldn't set breakpoint for symbol %s" % symbol)
    return bp

def helloTask(eventProcessor):
    log("Hello Task", "Hello World")
    
def resumeTask(eventProcessor):
    eventProcessor.resumeVotes = eventProcessor.resumeVotes + 1
    
    
def main(argv):            
    # launch the inferior and stop at the beginning of main
    eventProcessor = launch(argv)
    # register a hook listener that will be invoked everytime
    # the hook function is hit. If no other stop reasons where
    # found, the listener will vote for resuming the process
    ThreadListener(eventProcessor)
    try:
        # post a task to resume the inferior
        eventProcessor.postTask(resumeTask, "Resume", 5)
        
        # Now bombard the event process thread with tasks        
        while(True):    
            # post a few test tasks that interrupt the inferior
            eventProcessor.postTask(helloTask, "Hello task", 1)
            eventProcessor.postTask(helloTask, "Hello task", 1)
            eventProcessor.postTask(helloTask, "Hello task", 1)
            eventProcessor.postTask(helloTask, "Hello task", 1)
            eventProcessor.postTask(helloTask, "Hello task", 1)
            # sleep for 20ms
            time.sleep(0.02)
        
    finally:
        eventProcessor.terminate()
        lldb.SBDebugger.Terminate()        
            
if __name__ == '__main__':    
    main(sys.argv[1:])