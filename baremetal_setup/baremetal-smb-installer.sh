#!/bin/bash

sudo apt update
sudo apt install samba -y

sudo useradd -M -s /sbin/nologin "$USER" || true
sudo smbpasswd -a "$USER"

sudo systemctl restart smbd

sudo tee -a /etc/samba/smb.conf << 'EOF'

sudo systemctl restart smbd

# configure firewall (allow smb only on local network)
sudo ufw allow from 192.168.50.0/24 to any port 445 proto tcp
sudo ufw allow from 192.168.50.0/24 to any port 139 proto tcp
sudo ufw allow from 192.168.50.37 to any port 22 proto tcp
sudo ufw enable
