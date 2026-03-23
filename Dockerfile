# Utiliser une image Python officielle légère
FROM python:3.11-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de dépendances
COPY requirements.txt .

# Installer les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le code du projet
COPY . .

# Exposer le port du serveur Flask
EXPOSE 5000

# Lancer l'application
CMD ["python", "serveur.py"]
