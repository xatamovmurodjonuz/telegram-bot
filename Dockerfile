# Asosiy image sifatida python 3.12 slim versiyasini olamiz
FROM python:3.12-slim

# Kerakli Linux paketlarini o'rnatamiz (pg_config uchun libpq-dev va kompilyator)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Ishlash katalogini yaratamiz va belgilaymiz
WORKDIR /app

# Lokal requirements.txt ni konteynerga nusxalash
COPY requirements.txt .

# Virtual muhit yaratib, pip yangilab, kerakli kutubxonalarni o'rnatamiz
RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

# Butun loyihani konteynerga nusxalash
COPY . .

# Botni ishga tushirish uchun komandani belgilaymiz
CMD ["/opt/venv/bin/python", "bot.py"]
