#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>

// socket
#define BUFFER_SIZE 1024
#define SERVER_IP "127.0.0.1"
#define SERVER_PORT 8000

// Button
#define BUFFER_MAX 3
#define VALUE_MAX 30
#define DIRECTION_MAX 35

#define IN 0
#define OUT 1
#define LOW 0
#define HIGH 1

#define BUTTON_PIN 20

void error_handling(char *message) {
    fputs(message, stderr);
    fputc('\n', stderr);
    exit(1);
}

// GPIO 핀 export
static int GPIOExport(int pin) {
    char buffer[BUFFER_MAX];
    int fd = open("/sys/class/gpio/export", O_WRONLY);
    if (fd == -1) {
        fprintf(stderr, "Failed to open export for writing!\n");
        return -1;
    }
    snprintf(buffer, BUFFER_MAX, "%d", pin);
    write(fd, buffer, strlen(buffer));
    close(fd);
    return 0;
}

// GPIO 핀 direction 설정
static int GPIODirection(int pin, int dir) {
    char path[DIRECTION_MAX];
    int fd;
    snprintf(path, DIRECTION_MAX, "/sys/class/gpio/gpio%d/direction", pin);
    fd = open(path, O_WRONLY);
    if (fd == -1) {
        fprintf(stderr, "Failed to open gpio direction for writing!\n");
        return -1;
    }
    if (dir == IN) {
        write(fd, "in", 2);
    } else {
        write(fd, "out", 3);
    }
    close(fd);
    return 0;
}

// GPIO 값 읽기
static int GPIORead(int pin) {
    char path[VALUE_MAX];
    char value_str[3];
    int fd;

    snprintf(path, VALUE_MAX, "/sys/class/gpio/gpio%d/value", pin);
    fd = open(path, O_RDONLY);
    if (fd == -1) {
        fprintf(stderr, "Failed to open gpio value for reading!\n");
        return -1;
    }
    if (read(fd, value_str, 3) == -1) {
        fprintf(stderr, "Failed to read value!\n");
        close(fd);
        return -1;
    }
    close(fd);
    return atoi(value_str);
}

int button_state;
int sending;

void* connection() 
{
    int sockfd;
    struct sockaddr_in server_addr;
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(SERVER_PORT);
    server_addr.sin_addr.s_addr = inet_addr(SERVER_IP);

    while (1) 
    {
        sleep(2);
        if ((sockfd = socket(AF_INET, SOCK_STREAM, 0)) == -1) {
            perror("[Socket] Socket creation failed");
            continue;
        }

        if (connect(sockfd, (struct sockaddr *)&server_addr, sizeof(server_addr)) == -1) {
            perror("Connection failed... try again!\n");
            continue;
        }

        printf("[*] Connection Established\n");
        
        while(1)
        {
            char buffer[3];
            snprintf(buffer, sizeof(buffer), "%d", button_state);

            if (send(sockfd, buffer, strlen(buffer), 0) == -1) 
            {
                perror("Send failed : Connection Closed.");
                break; // 연결이 끊어졌으니 다시 재연결하기
            }

            printf("Data sent: %s\n", buffer);
            usleep(500000);
        }
    }

    close(sockfd);
    return NULL;
}

#define BUTTON_IDLE 1
#define BUTTON_PRESS 0

void* button_detect() 
{
    if (GPIOExport(BUTTON_PIN) == -1) return NULL;

    if (GPIODirection(BUTTON_PIN, IN) == -1) return NULL;

    // 초기 상태는 버튼이 눌리지 않았을 때
    int prev_state = 1;  // 1 means button is not pressed initially
    button_state = 1;  // 1 means button is in idle state (not pressed)

    while (1)
    {
        // 버튼 상태를 읽음
        int current_state = GPIORead(BUTTON_PIN);
        if (current_state == -1) return NULL;

        // 버튼 상태가 변화했을 때만 처리
        if (current_state == 0 && prev_state == 1) {
            // 버튼이 눌렸을 때 (상태 변화)
            button_state = (button_state == 0) ? 1 : 0;  // 토글 상태
            printf("Button state toggled: %d\n", button_state);
            prev_state = 0;  // 버튼이 눌린 상태로 변경
        } else if (current_state == 1 && prev_state == 0) {
            // 버튼이 떼어졌을 때
            prev_state = 1;  // 버튼이 떼어짐
        }

        usleep(100000);  // 100ms 대기
    }
    return NULL;
}

// void* button_detect() 
// {
//     if (GPIOExport(BUTTON_PIN) == -1) return NULL;

//     if (GPIODirection(BUTTON_PIN, IN) == -1) return NULL;

//     while (1)
//     {
//         // 버튼을 누르지 않는다면 기다리고 있는다.
//         if (GPIOExport(BUTTON_PIN) == -1)
//             return NULL;

//         if (GPIODirection(BUTTON_PIN, IN) == -1)
//             return NULL;

//         int prev_state = 0;

//         while (1)
//         {
//             button_state = GPIORead(BUTTON_PIN);
//             if (button_state == -1) return NULL;
//             if(button_state == 0)  usleep(500000);
//         }
//         return NULL;
//     }
//     return NULL;
// }

int main(int argc, char *argv[]) {
    pthread_t button_thread;
    pthread_t socket_thread;

    pthread_create(&socket_thread, NULL, connection, NULL);
    pthread_create(&button_thread, NULL, button_detect, NULL);

    pthread_join(socket_thread, NULL);
    pthread_join(button_thread, NULL);

    return 0;
}