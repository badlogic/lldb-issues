#/bin/sh
gcc -g -lpthread test.c
python issue-219.py a.out
