#/bin/sh
gcc -g -lpthread test.c
python issue219.py a.out
