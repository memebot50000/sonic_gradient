import http.server
import socketserver
import json
from urllib.parse import urlparse, parse_qs
import webbrowser
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import fitbit
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import sqlite3
import datetime
import os

# Spotify API credentials
SPOTIFY_CLIENT_ID = "your_spotify_client_id"
SPOTIFY_CLIENT_SECRET = "your_spotify_client_secret"
SPOTIFY_REDIRECT_URI = "http://localhost:8000/spotify_callback"

# Fitbit API credentials
FITBIT_CLIENT_ID = "your_fitbit_client_id"
FITBIT_CLIENT_SECRET = "your_fitbit_client_secret"
FITBIT_REDIRECT_URI = "http://localhost:8000/fitbit_callback"

class SonicGradient:
    def __init__(self):
        self.spotify = None
        self.fitbit_client = None
        self.db_conn = sqlite3.connect('biometric_data.db', check_same_thread=False)
        self.create_table()
        self.model = GradientBoostingRegressor()
        self.scaler = StandardScaler()
        self.target_hr = None
        self.optimize_direction = None

    def setup_spotify(self):
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-read-playback-state user-modify-playback-state user-read-currently-playing streaming user-library-read"
        )
        self.spotify = spotipy.Spotify(auth_manager=auth_manager)
        return auth_manager

    def setup_fitbit(self):
        self.fitbit_client = fitbit.Fitbit(
            FITBIT_CLIENT_ID,
            FITBIT_CLIENT_SECRET,
            redirect_uri=FITBIT_REDIRECT_URI,
            oauth2=True
        )
        return self.fitbit_client

    def create_table(self):
        cursor = self.db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS biometric_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id TEXT,
                heart_rate INTEGER,
                task TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.db_conn.commit()

    def get_current_heart_rate(self):
        now = datetime.datetime.now()
        heart_rate = self.fitbit_client.intraday_time_series('activities/heart', base_date=now.date(), detail_level='1sec')
        return heart_rate['activities-heart-intraday']['dataset'][-1]['value']

    def handle_hr_data(self):
        heart_rate = self.get_current_heart_rate()
        current_track = self.spotify.current_user_playing_track()
        if current_track and heart_rate:
            song_id = current_track['item']['id']
            task = 'current_task'
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO biometric_data (song_id, heart_rate, task)
                VALUES (?, ?, ?)
            ''', (song_id, heart_rate, task))
            self.db_conn.commit()
            
            if self.target_hr:
                if self.optimize_direction == 'minimize' and heart_rate < self.target_hr:
                    self.recommend_and_play_song()
                elif self.optimize_direction == 'maximize' and heart_rate > self.target_hr:
                    self.recommend_and_play_song()

    def train_model(self):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT song_id, heart_rate, task FROM biometric_data')
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['song_id', 'heart_rate', 'task'])
        
        X = pd.get_dummies(df[['song_id', 'task']])
        y = df['heart_rate']
        
        X_scaled = self.scaler.fit_transform(X)
        X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2)
        
        self.model.fit(X_train, y_train)
        score = self.model.score(X_test, y_test)
        print(f"Model R-squared score: {score}")

    def recommend_song(self):
        top_tracks = self.spotify.current_user_saved_tracks(limit=50)
        predictions = []
        for track in top_tracks['items']:
            X_pred = pd.get_dummies(pd.DataFrame({'song_id': [track['track']['id']], 'task': ['current_task']}))
            X_pred = X_pred.reindex(columns=self.model.feature_names_in_, fill_value=0)
            X_pred_scaled = self.scaler.transform(X_pred)
            pred_hr = self.model.predict(X_pred_scaled)
            predictions.append((track['track']['id'], pred_hr[0]))
        
        if self.optimize_direction == 'minimize':
            best_track_id = min(predictions, key=lambda x: x[1])[0]
        else:
            best_track_id = max(predictions, key=lambda x: x[1])[0]
        return self.spotify.track(best_track_id)

    def recommend_and_play_song(self):
        recommended_track = self.recommend_song()
        self.play_song(recommended_track['id'])

    def play_song(self, song_id):
        self.spotify.start_playback(uris=[f"spotify:track:{song_id}"])

    def get_liked_songs(self):
        results = self.spotify.current_user_saved_tracks()
        return [{'id': track['track']['id'], 'name': track['track']['name']} for track in results['items']]

    def get_heart_rate_data(self, song_id):
        cursor = self.db_conn.cursor()
        cursor.execute('SELECT heart_rate, timestamp FROM biometric_data WHERE song_id = ?', (song_id,))
        data = cursor.fetchall()
        return [{'heart_rate': hr, 'timestamp': ts} for hr, ts in data]

sonic_gradient = SonicGradient()
spotify_auth_manager = sonic_gradient.setup_spotify()
fitbit_client = sonic_gradient.setup_fitbit()

class SonicGradientHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open('sonic_gradient.html', 'rb') as file:
                self.wfile.write(file.read())
        elif parsed_path.path == '/get_liked_songs':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            liked_songs = sonic_gradient.get_liked_songs()
            self.wfile.write(json.dumps(liked_songs).encode())
        elif parsed_path.path == '/get_heart_rate_data':
            query = parse_qs(parsed_path.query)
            song_id = query.get('song_id', [None])[0]
            if song_id:
                data = sonic_gradient.get_heart_rate_data(song_id)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            else:
                self.send_error(400, "Missing song_id parameter")
        elif parsed_path.path == '/spotify_callback':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"Spotify authentication successful. You can close this window.")
        elif parsed_path.path == '/fitbit_callback':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"Fitbit authentication successful. You can close this window.")

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        parsed_data = parse_qs(post_data)
        
        if self.path == '/play_song':
            song_id = parsed_data.get('song_id', [None])[0]
            if song_id:
                sonic_gradient.play_song(song_id)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'success'}).encode())
            else:
                self.send_error(400, "Missing song_id parameter")
        elif self.path == '/set_target_hr':
            target_hr = parsed_data.get('target_hr', [None])[0]
            optimize_direction = parsed_data.get('optimize_direction', [None])[0]
            if target_hr and optimize_direction:
                sonic_gradient.target_hr = int(target_hr)
                sonic_gradient.optimize_direction = optimize_direction
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'success'}).encode())
            else:
                self.send_error(400, "Missing parameters")

if __name__ == "__main__":
    PORT = 8000
    Handler = SonicGradientHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}")
        webbrowser.open(f"http://localhost:{PORT}")
        httpd.serve_forever()
