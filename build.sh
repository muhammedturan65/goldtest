#!/usr/bin/env bash
# exit on error
set -o errexit

# Poetry'nin önbelleğini temizlemesiyle ilgili olası sorunları önlemek için
# pip önbellek dizinini proje dizini içinde ayarla
pip config set global.cache-dir "$(pwd)/.cache/pip"

# Bağımlılıkları yükle
pip install -r requirements.txt

# Google Chrome'u yüklemek için gerekli paketleri kur
# YÖNETİCİ İZNİ GEREKTİĞİ İÇİN BAŞLARINA 'sudo' EKLENDİ
sudo apt-get update
sudo apt-get install -y wget unzip fontconfig

# En son kararlı Chrome sürümünü indir ve kur
wget -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb

# YÖNETİCİ İZNİ GEREKTİĞİ İÇİN BAŞINA 'sudo' EKLENDİ
sudo apt-get install -y /tmp/google-chrome.deb
rm /tmp/google-chrome.deb
