import os
import time
import threading
from collections import defaultdict
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ---------- CONFIGURATION (via variables d'environnement Render) ----------
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "change-moi")
# ---------------------------------------------------------------------------

TAILLE_BLOC = 480
TAILLE_LOT = 3
DELAI_ENTRE_ENVOIS = 8          # secondes entre chaque bloc d'un même lot
PAUSE_ENTRE_LOTS = 20 * 60      # 20 minutes entre deux lots

# Limite simple : si le jeton fuite un jour, ça évite un spam massif du webhook.
LIMITE_PAR_HEURE = 5
historique_ip = defaultdict(list)
verrou = threading.Lock()


def ip_autorisee(ip):
    maintenant = time.time()
    with verrou:
        historique_ip[ip] = [t for t in historique_ip[ip] if maintenant - t < 3600]
        if len(historique_ip[ip]) >= LIMITE_PAR_HEURE:
            return False
        historique_ip[ip].append(maintenant)
        return True


def decouper_texte(texte, taille_max):
    """Découpe le texte en blocs de taille_max caractères max, sans couper les mots."""
    mots = texte.split(" ")
    blocs = []
    bloc_actuel = ""
    for mot in mots:
        candidat = f"{bloc_actuel} {mot}".strip() if bloc_actuel else mot
        if len(candidat) <= taille_max:
            bloc_actuel = candidat
        else:
            if bloc_actuel:
                blocs.append(bloc_actuel)
            while len(mot) > taille_max:  # cas rare : un "mot" plus long que la limite
                blocs.append(mot[:taille_max])
                mot = mot[taille_max:]
            bloc_actuel = mot
    if bloc_actuel:
        blocs.append(bloc_actuel)
    return blocs


def envoyer_discord(contenu):
    try:
        r = requests.post(WEBHOOK_URL, json={"content": contenu}, timeout=10)
        return r.status_code == 204
    except Exception as e:
        print(f"[erreur webhook] {e}")
        return False


def traiter_envoi(texte):
    blocs = decouper_texte(texte, TAILLE_BLOC)
    index = 0
    while index < len(blocs):
        fin_lot = min(index + TAILLE_LOT, len(blocs))
        for i in range(index, fin_lot):
            ok = envoyer_discord(blocs[i])
            print(f"Bloc {i + 1}/{len(blocs)} : {'envoyé' if ok else 'échec'}")
            if i < fin_lot - 1:
                time.sleep(DELAI_ENTRE_ENVOIS)
        index = fin_lot
        if index < len(blocs):
            print(f"Pause de 20 minutes ({len(blocs) - index} blocs restants)...")
            time.sleep(PAUSE_ENTRE_LOTS)
    print("Terminé.")


@app.route("/statut", methods=["GET"])
def statut():
    # Endpoint public volontairement : sert au site à savoir si le serveur
    # tourne, et au ping automatique qui empêche Render de s'endormir.
    return jsonify({"etat": "en ligne"})


@app.route("/envoyer", methods=["POST"])
def envoyer():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ACCESS_TOKEN}":
        return jsonify({"erreur": "non autorisé"}), 401

    ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    if not ip_autorisee(ip):
        return jsonify({"erreur": "trop de tentatives, réessaie plus tard"}), 429

    if not WEBHOOK_URL:
        return jsonify({"erreur": "webhook non configuré sur le serveur (variable DISCORD_WEBHOOK_URL)"}), 500

    data = request.get_json(silent=True) or {}
    texte = (data.get("texte") or "").strip()
    if not texte:
        return jsonify({"erreur": "texte vide"}), 400

    blocs = decouper_texte(texte, TAILLE_BLOC)

    # traitement en arrière-plan : la page peut être fermée juste après l'envoi
    threading.Thread(target=traiter_envoi, args=(texte,), daemon=True).start()

    return jsonify({"statut": "reçu", "blocs": len(blocs)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
