#!/bin/bash
set -e
ls -l
sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
ssh-keygen -f /runner/id_rsa -q -P ""
cd /runner
sudo docker-compose up -d