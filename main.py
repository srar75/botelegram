from flask import Flask
import requests
import time
from datetime import datetime
import telebot
from telebot.apihelper import ApiTelegramException
import json
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os


# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuración
TELEGRAM_TOKEN = "8154833975:AAEpqS9VjsnbTPMygi0SHesPB5FIQOIqH8E"
CHANNEL_ID = "-1002326247352"
FOOTBALL_API_KEY = "f6f4413f249c46bcb48d5b82b5879122"
FOOTBALL_API_BASE = "https://api.football-data.org/v4"

# Configuración de tiempos
UPDATE_INTERVAL = 5      # Reducido a 5 segundos
MESSAGE_COOLDOWN = 15     # Reducido a 15 segundos para evitar duplicados
REQUEST_TIMEOUT = 15      # Timeout para peticiones HTTP
DUPLICATE_WINDOW = 60    # Ventana de tiempo para detectar duplicados (1 minutos)

# Configuración del pool de hilos
MAX_WORKERS = 8 # increased max workers to 8

# Configuración de reintentos
retry_strategy = Retry(
    total=3,              # número total de reintentos
    backoff_factor=1,     # tiempo de espera entre reintentos
    status_forcelist=[429, 500, 502, 503, 504]  # códigos de error HTTP para reintentar
)

# Configurar la sesión con reintentos
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
session.mount("http://", adapter)
session.mount("https://", adapter)
session.headers.update({'X-Auth-Token': FOOTBALL_API_KEY})

# Mapeo directo de competiciones con sus banderas
LEAGUE_FLAGS = {
    # Ligas Principales
    'Premier League': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'La Liga': '🇪🇸',
    'Primera Division': '🇪🇸',  # Añadido para variante del nombre
    'LaLiga': '🇪🇸',           # Añadido para variante del nombre
    'Primera División': '🇪🇸',  # Añadido con tilde
    'Bundesliga': '🇩🇪',
    'Serie A': '🇮🇹',
    'Ligue 1': '🇫🇷',
    'Primeira Liga': '🇵🇹',
    'Eredivisie': '🇳🇱',

    # Otras Ligas Europeas
    'Championship': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'Segunda División': '🇪🇸',
    'Serie B': '🇮🇹',
    'Ligue 2': '🇫🇷',
    'Bundesliga 2': '🇩🇪',
    'Scottish Premiership': '🏴󠁧󠁢󠁳󠁣󠁴󠁿',
    'Belgian Pro League': '🇧🇪',
    'Super League Greece': '🇬🇷',
    'Turkish Süper Lig': '🇹🇷',

    # Ligas Americanas
    'Brazilian Championship Series A': '🇧🇷',
    'Liga MX': '🇲🇽',
    'MLS': '🇺🇸',
    'Argentine Primera División': '🇦🇷',
    'Primera A': '🇨🇴',
    'Liga Profesional': '🇦🇷',

    # Ligas Asiáticas
    'Saudi Pro League': '🇸🇦',
    'Chinese Super League': '🇨🇳',
    'J1 League': '🇯🇵',
    'K League 1': '🇰🇷',

    # Copas y Competiciones Internacionales
    'UEFA Champions League': '🇪🇺',
    'UEFA Europa League': '🇪🇺',
    'UEFA Conference League': '🇪🇺',
    'UEFA Europa Conference League': '🇪🇺',
    'UEFA Super Cup': '🇪🇺',
    'FIFA World Cup': '🌍',
    'FIFA Club World Cup': '🌍',
    'Copa Libertadores': '🌍',
    'Copa Sudamericana': '🌍',
    'FA Cup': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'Copa del Rey': '🇪🇸',
    'DFB Pokal': '🇩🇪',
    'Coppa Italia': '🇮🇹',
    'Coupe de France': '🇫🇷',
    'KNVB Beker': '🇳🇱',
    'Taça de Portugal': '🇵🇹',

    # Supercopas
    'Community Shield': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'Supercopa de España': '🇪🇸',
    'DFL-Supercup': '🇩🇪',
    'Supercoppa Italiana': '🇮🇹',
    'Trophée des Champions': '🇫🇷',
    'Johan Cruijff Schaal': '🇳🇱',
}

# Bandera por defecto para competiciones no listadas
DEFAULT_FLAG = '🏳️'

# Inicialización del bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

class TelegramChannel:
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.verify_bot_permissions()

    def verify_bot_permissions(self):
        try:
            test_message = self.bot.send_message(
                self.channel_id,
                "🤖 Bot de fútbol iniciado y conectado al canal correctamente.",
                parse_mode='Markdown'
            )
            self.bot.delete_message(self.channel_id, test_message.message_id)
            logger.info("Bot conectado exitosamente al canal")
        except ApiTelegramException as e:
            if e.error_code == 403:
                logger.error("El bot no tiene permisos para publicar en el canal")
                raise Exception("El bot necesita ser administrador del canal")
            else:
                logger.error(f"Error al verificar permisos: {str(e)}")
                raise

    def send_message(self, message):
        try:
            return self.bot.send_message(
                self.channel_id,
                message,
                parse_mode='Markdown'
            )
        except ApiTelegramException as e:
            logger.error(f"Error al enviar mensaje: {str(e)}")
            raise
class MatchTracker:
    def __init__(self):
        self.tracked_matches = {}
        self.message_history = {}  # Para evitar duplicados
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        try:
            self.telegram_channel = TelegramChannel(bot, CHANNEL_ID)
            logger.info("MatchTracker iniciado correctamente")
        except Exception as e:
            logger.error(f"Error al inicializar MatchTracker: {str(e)}")
            raise

    def get_league_flag(self, competition_name):
        """Obtiene la bandera correspondiente a la competición."""
        # Manejo especial para variantes de La Liga
        if any(x in competition_name for x in ['La Liga', 'Primera', 'LaLiga']):
            return '🇪🇸'
        return LEAGUE_FLAGS.get(competition_name, DEFAULT_FLAG)

    def get_live_matches(self):
      try:
          response = session.get(
              f"{FOOTBALL_API_BASE}/matches",
              timeout=REQUEST_TIMEOUT,
              verify=True
          )
          if response.status_code == 200:
              matches = response.json()['matches']
              return [m for m in matches if m['status'] in ['SCHEDULED', 'LIVE', 'IN_PLAY', 'PAUSED']]
          else:
              logger.error(f"Error al obtener partidos: {response.status_code}")
              return []
      except requests.Timeout:
          logger.error("Timeout al obtener partidos. Reintentando...")
          return []
      except Exception as e:
          logger.error(f"Error en get_live_matches: {str(e)}")
          return []

    def get_match_details(self, match_id):
        try:
            response = session.get(
                f"{FOOTBALL_API_BASE}/matches/{match_id}",
                timeout=REQUEST_TIMEOUT,
                verify=True
            )
            if response.status_code == 200:
                match_data = response.json()
                if 'goals' in match_data:
                    match_data['goals'] = sorted(match_data['goals'], key=lambda x: x['minute'])
                return match_data
            else:
                logger.error(f"Error al obtener detalles del partido: {response.status_code}")
                return None
        except requests.Timeout:
            logger.error("Timeout al obtener detalles del partido. Reintentando...")
            return None
        except Exception as e:
            logger.error(f"Error en get_match_details: {str(e)}")
            return None

    def is_duplicate_message(self, message_key, content):
        """Verifica si un mensaje es duplicado dentro de la ventana de tiempo."""
        current_time = time.time()
        if message_key in self.message_history:
            last_time, last_content = self.message_history[message_key]
            if current_time - last_time < DUPLICATE_WINDOW and last_content == content:
                return True
        self.message_history[message_key] = (current_time, content)

        # Limpia mensajes antiguos del historial
        self.message_history = {
            k: v for k, v in self.message_history.items()
            if current_time - v[0] < DUPLICATE_WINDOW
        }
        return False

    def send_message(self, message, message_type, match_id):
        message_key = f"{match_id}_{message_type}"

        # Verifica duplicados
        if not self.is_duplicate_message(message_key, message):
            try:
                self.telegram_channel.send_message(message)
                logger.info(f"Mensaje enviado ({message_type})")
            except Exception as e:
                logger.error(f"Error al enviar mensaje: {str(e)}")

    async def process_match(self, match):
        try:
            match_id = match['id']
            current_status = match['status']
            home_team = match['homeTeam']
            away_team = match['awayTeam']
            home_team_name = home_team['shortName'] or home_team['name']
            away_team_name = away_team['shortName'] or away_team['name']
            competition_name = match['competition']['name']
            league_flag = self.get_league_flag(competition_name)
            current_home_score = match['score'].get('fullTime', {}).get('home', 0) or 0
            current_away_score = match['score'].get('fullTime', {}).get('away', 0) or 0

            if match_id not in self.tracked_matches:
                self.tracked_matches[match_id] = {
                    'status': current_status,
                    'home_score': current_home_score,
                    'away_score': current_away_score,
                    'half_time_notified': False,
                    'second_half_notified': False,
                    'goals': set(),
                    'finished_notified': False,
                    'home_team_id': home_team['id'],
                    'away_team_id': away_team['id']
                }

                if current_status in ['LIVE', 'IN_PLAY']:
                    message = (
                        f"🏆 *INICIO DEL PARTIDO*\n"
                        f"{competition_name} {league_flag}\n"
                        f"{home_team_name} vs {away_team_name}"
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor, self.send_message, message, 'start', match_id
                    )

            else:
                tracked_match = self.tracked_matches[match_id]
                previous_status = tracked_match['status']

                # Verificar segundo tiempo
                if (previous_status == 'PAUSED' and 
                    current_status == 'IN_PLAY' and 
                    tracked_match['half_time_notified'] and 
                    not tracked_match['second_half_notified']):
                    message = (
                        f"▶️ *INICIO SEGUNDO TIEMPO*\n"
                        f"{competition_name} {league_flag}\n"
                        f"{home_team_name} {current_home_score} - {current_away_score} {away_team_name}"
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor, self.send_message, message, 'second_half', match_id
                    )
                    tracked_match['second_half_notified'] = True

                # Verificar goles
                if (current_home_score != tracked_match['home_score'] or 
                    current_away_score != tracked_match['away_score']):
                    match_details = await asyncio.get_event_loop().run_in_executor(
                        self.executor, self.get_match_details, match_id
                    )

                    if match_details and 'goals' in match_details:
                        for goal in match_details['goals']:
                            goal_id = f"{match_id}_{goal['minute']}_{goal.get('scorer', {}).get('id', '')}"

                            if goal_id not in tracked_match['goals']:
                                scorer = goal.get('scorer', {}).get('name', 'Gol en propia puerta')
                                minute = goal['minute']

                                message = (
                                    f"⚽ *GOL*\n"
                                    f"{competition_name} {league_flag}\n"
                                    f"⚽ {scorer} ({minute}')\n"
                                    f"{home_team_name} {current_home_score} - {current_away_score} {away_team_name}"
                                )
                                await asyncio.get_event_loop().run_in_executor(
                                    self.executor, self.send_message, message, f'goal_{goal_id}', match_id
                                )
                                tracked_match['goals'].add(goal_id)

                    tracked_match['home_score'] = current_home_score
                    tracked_match['away_score'] = current_away_score

                # Verificar medio tiempo
                if current_status == 'PAUSED' and not tracked_match['half_time_notified']:
                    message = (
                        f"⏸ *MEDIO TIEMPO*\n"
                        f"{competition_name} {league_flag}\n"
                        f"{home_team_name} {current_home_score} - {current_away_score} {away_team_name}"
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor, self.send_message, message, 'half_time', match_id
                    )
                    tracked_match['half_time_notified'] = True

                # Verificar final del partido
                if (current_status in ['FINISHED', 'COMPLETED'] and 
                    not tracked_match['finished_notified']):
                    message = (
                        f"🔚 *FINAL DEL PARTIDO*\n"
                        f"{competition_name} {league_flag}\n"
                        f"{home_team_name} {current_home_score} - {current_away_score} {away_team_name}"
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor, self.send_message, message, 'end', match_id
                    )
                    tracked_match['finished_notified'] = True

                tracked_match['status'] = current_status

        except Exception as e:
            logger.error(f"Error procesando partido {match_id}: {str(e)}")

    async def check_match_updates(self):
        try:
            live_matches = await asyncio.get_event_loop().run_in_executor(
                self.executor, self.get_live_matches
            )

            # Procesar partidos en paralelo
            await asyncio.gather(
                *[self.process_match(match) for match in live_matches]
            )

            # Limpiar partidos terminados
            current_match_ids = {match['id'] for match in live_matches}
            for match_id in list(self.tracked_matches.keys()):
                if match_id not in current_match_ids:
                    del self.tracked_matches[match_id]

        except Exception as e:
            logger.error(f"Error en check_match_updates: {str(e)}")


# Flask app setup
app = Flask(__name__)

@app.route("/")
def health_check():
    return "¡Estoy vivo!", 200

async def main():
    try:
      tracker = MatchTracker()
      logger.info("Bot iniciado correctamente")

      # Mensaje de inicio
      tracker.send_message(
          "🤖 *Bot de Fútbol Iniciado*\nMonitoreando partidos en vivo...", 
          'bot_start', 
          0
      )

      while True:
        await tracker.check_match_updates()
        await asyncio.sleep(UPDATE_INTERVAL)
    except Exception as e:
        logger.error(f"Error en el loop principal: {str(e)}")
        await asyncio.sleep(UPDATE_INTERVAL)


def run_bot():
    asyncio.run(main())

# Run the bot in a separate thread
executor = ThreadPoolExecutor(max_workers=1)
executor.submit(run_bot)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
