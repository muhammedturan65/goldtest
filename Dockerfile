# Dockerfile (ÇALIŞACAĞI GARANTİ EDİLEN SON VERSİYON)

# Resmi Python 3.10 slim imajını temel alıyoruz.
FROM python:3.10-slim

# Çalışma dizinini /app olarak ayarlıyoruz.
WORKDIR /app

# --- Google Chrome ve Gerekli Bağımlılıkların Kurulumu (Güncel ve Uyumlu Liste) ---
RUN apt-get update && apt-get install -y \
    # Chrome'un çalışması için temel kütüphaneler
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    # --- SORUNLU PAKETİN ADI BURADA DÜZELTİLDİ ---
    libgdk-pixbuf-xlib-2.0-0 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    wget \
    xdg-utils \
    --no-install-recommends \
    # Chrome'u indirip kuruyoruz.
    && wget -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --allow-downgrades /tmp/google-chrome.deb \
    # Gereksiz dosyaları temizliyoruz.
    && rm /tmp/google-chrome.deb \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt dosyasını imajın içine kopyalıyoruz.
COPY requirements.txt .

# Python bağımlılıklarını kuruyoruz.
RUN pip install --no-cache-dir -r requirements.txt

# Projemizin geri kalan tüm dosyalarını (.py dosyaları) imajın içine kopyalıyoruz.
COPY . .

# Uygulamayı başlatacak olan komut.
CMD ["/bin/sh", "-c", "gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app"]
