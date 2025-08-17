# Dockerfile (Selenium ve Chrome Kaldırılmış, Süper Hızlı Versiyon)

# Resmi Python 3.10 slim imajını temel alıyoruz.
FROM python:3.10-slim

# Çalışma dizinini /app olarak ayarlıyoruz.
WORKDIR /app

# requirements.txt dosyasını imajın içine kopyalıyoruz.
COPY requirements.txt .

# Python bağımlılıklarını kuruyoruz.
# Artık Chrome kurulumu yok!
RUN pip install --no-cache-dir -r requirements.txt

# Projemizin geri kalan tüm dosyalarını (.py dosyaları) imajın içine kopyalıyoruz.
COPY . .

# Uygulamayı başlatacak olan komut.
CMD ["/bin/sh", "-c", "gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app"]
