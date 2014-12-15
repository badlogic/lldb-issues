#include <stdio.h>
#include <unistd.h>
#include <pthread.h>

void __nsleep(const struct timespec *req, struct timespec *rem)
{
    struct timespec temp_rem;
    if(nanosleep(req,rem)==-1)
        __nsleep(rem,&temp_rem);
}
 
void msleep(unsigned long milisec)
{
    struct timespec req={0},rem={0};
    time_t sec=(int)(milisec/1000);
    milisec=milisec-(sec*1000);
    req.tv_sec=sec;
    req.tv_nsec=milisec*1000000L;
    __nsleep(&req,&rem);    
}

void threadStart(pthread_t* thread) {
    printf("thread started\n");
}

void threadEnd(pthread_t* thread) {
    printf("thread ended\n");
}

void* doSomething(void* thread) {
    threadStart((pthread_t*)thread);
    printf("Doing something amazing\n");    
    msleep(20);  
    threadEnd((pthread_t*)thread);
    return 0;
}

int main(int argc, char** argv) {
    int i = 5;
    while(1) {
        pthread_t thread;
        pthread_create(&thread, 0, doSomething, &thread);
        pthread_join(thread, 0);        
    }
}
