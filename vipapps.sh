#!/bin/bash

export PYTHONPATH="$HOME/gitwork/VIP-python-client/src"

export VIP_API_URL="http://192.168.122.112:8080"
export VIP_API_KEY="$(ssh admin@vm4 ./mysql/bin/mariadb vip -Ne $(printf "%q " "SELECT apikey FROM VIPUsers WHERE email='admin@example.com';"))"

python3 $HOME/gitwork/VIP-python-client/vipapps.py
