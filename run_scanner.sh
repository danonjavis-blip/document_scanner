#!/bin/bash
# Запуск сканера: стартует сервер (если ещё не запущен) и открывает Firefox.
cd "$(dirname "$0")"

if ! curl -s -o /dev/null http://localhost:5050/ ; then
    nohup python3 scanner_app.py > scanner.log 2>&1 &
    for i in $(seq 1 40); do
        curl -s -o /dev/null http://localhost:5050/ && break
        sleep 0.3
    done
fi

firefox http://localhost:5050
