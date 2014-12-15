lldb-issues
===========

Collection of Python scripts to reproduce issues with LLDB.

## Issue 219
### Description
TODO

### Reproduction steps
Run `run.sh` on the CLI. This will compile `test.c` and then invoke the `issue-219.py` script. The script should crash
after 5 seconds. If not, `CTRL+C` and restart until the script crashes.

You can also run the script from within the LLDB cli client:

```
(lldb) script
>>> import issue219
>>> issue219.main(["a.out"])
```

You can attach Xcode to `lldb` before running the script for debugging.

