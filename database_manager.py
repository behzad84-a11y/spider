import sqlite3
import os
import logging
import time  # Verified: Essential for instance lock timing
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_name='trades.db'):
        # Use DB_DIR env var if present (for VPS releases architecture), else use script dir
        base_dir = os.environ.get('DB_DIR')
        if not base_dir:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        db_path = os.path.join(base_dir, db_name)
        
        # Ensure directory exists
        if not os.path.exists(os.path.dirname(db_path)):
             os.makedirs(os.path.dirname(db_path), exist_ok=True)
             
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_table()

    def create_table(self):
        # Configuration/Settings table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # Active Strategies State
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id TEXT PRIMARY KEY,
                symbol TEXT,
                market_type TEXT,
                side TEXT,
                amount REAL,
                leverage INTEGER,
                state TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Trade History
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                pnl REAL,
                close_price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Equity Snapshots
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_equity REAL,
                spot_equity REAL,
                futures_equity REAL,
                unrealized_pnl REAL,
                net_deposits REAL DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Candle History for Stats (5m timeframe)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS candle_history (
                symbol TEXT,
                timestamp INTEGER,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY (symbol, timestamp)
            )
        ''')
        # GLN Guard (Fail-safe state)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS gln_guard (
                strategy_type TEXT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT 1,
                consecutive_losses INTEGER DEFAULT 0,
                disabled_until DATETIME,
                last_update DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Single-Active Instance Lock
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS instance_lock (
                id INTEGER PRIMARY KEY CHECK (id=1),
                owner_id TEXT,
                host TEXT,
                pid INTEGER,
                started_at INTEGER,
                last_ping INTEGER
            )
        ''')
        self.conn.commit()

    def acquire_instance_lock(self, owner_id, host, pid):
        """Attempts to acquire the global instance lock."""
        now = int(time.time())
        # Check existing lock
        self.cursor.execute('SELECT last_ping FROM instance_lock WHERE id=1')
        row = self.cursor.fetchone()
        
        if row:
            last_ping = row[0]
            if now - last_ping < 20: # 20 second timeout
                return False
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO instance_lock (id, owner_id, host, pid, started_at, last_ping)
            VALUES (1, ?, ?, ?, ?, ?)
        ''', (owner_id, host, pid, now, now))
        self.conn.commit()
        return True

    def update_instance_ping(self, owner_id):
        """Updates heartbeat for the active instance."""
        self.cursor.execute('''
            UPDATE instance_lock 
            SET last_ping = ? 
            WHERE id=1 AND owner_id = ?
        ''', (int(time.time()), owner_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def release_instance_lock(self, owner_id):
        """Releases the lock on shutdown."""
        self.cursor.execute('DELETE FROM instance_lock WHERE id=1 AND owner_id = ?', (owner_id,))
        self.conn.commit()

    def get_active_instance(self):
        """Returns details of the current master."""
        self.cursor.execute('SELECT owner_id, host, pid, started_at, last_ping FROM instance_lock WHERE id=1')
        return self.cursor.fetchone()

    def set_setting(self, key, value):
        self.cursor.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, str(value)))
        self.conn.commit()

    def get_setting(self, key, default=None):
        self.cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def save_config(self, key, value):
        self.set_setting(key, value)

    def load_config(self, key):
        return self.get_setting(key)

    def get_today_stats(self):
        today = datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute('''
            SELECT COUNT(*), SUM(pnl), 
            SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END),
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END)
            FROM trade_history 
            WHERE date(timestamp) = ?
        ''', (today,))
        row = self.cursor.fetchone()
        return {
            'total_trades': row[0] or 0,
            'total_pnl': row[1] or 0,
            'buys': row[2] or 0,
            'sells': row[3] or 0
        }

    def save_strategy(self, strategy_id, symbol, market_type, side, amount, leverage, state):
        self.cursor.execute('''
            INSERT OR REPLACE INTO strategies (strategy_id, symbol, market_type, side, amount, leverage, state)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (strategy_id, symbol, market_type, side, amount, leverage, json.dumps(state) if isinstance(state, dict) else state))
        self.conn.commit()

    def load_strategies(self):
        self.cursor.execute('SELECT strategy_id, symbol, market_type, side, amount, leverage, state FROM strategies')
        rows = self.cursor.fetchall()
        strategies = []
        for r in rows:
            strategies.append({
                'id': r[0],
                'symbol': r[1],
                'market_type': r[2],
                'side': r[3],
                'amount': r[4],
                'leverage': r[5],
                'state': json.loads(r[6]) if (r[6] and r[6].startswith('{')) else r[6]
            })
        return strategies

    def delete_strategy(self, strategy_id):
        self.cursor.execute('DELETE FROM strategies WHERE strategy_id = ?', (strategy_id,))
        self.conn.commit()

    def save_trade_history(self, symbol, side, pnl, close_price):
        self.cursor.execute('''
            INSERT INTO trade_history (symbol, side, pnl, close_price)
            VALUES (?, ?, ?, ?)
        ''', (symbol, side, pnl, close_price))
        self.conn.commit()

    def save_equity_snapshot(self, total, spot, futures, unrealized, net_deposits=0):
        self.cursor.execute('''
            INSERT INTO equity_snapshots (total_equity, spot_equity, futures_equity, unrealized_pnl, net_deposits)
            VALUES (?, ?, ?, ?, ?)
        ''', (total, spot, futures, unrealized, net_deposits))
        self.conn.commit()

    def get_equity_snapshots(self, since=None, limit=100):
        if since:
            self.cursor.execute('''
                SELECT total_equity, spot_equity, futures_equity, unrealized_pnl, net_deposits, timestamp 
                FROM equity_snapshots WHERE timestamp >= ? ORDER BY timestamp ASC
            ''', (since,))
        else:
            self.cursor.execute('''
                SELECT total_equity, spot_equity, futures_equity, unrealized_pnl, net_deposits, timestamp 
                FROM equity_snapshots ORDER BY timestamp DESC LIMIT ?
            ''', (limit,))
        return self.cursor.fetchall()

    def get_latest_equity_snapshot(self):
        self.cursor.execute('''
            SELECT total_equity, spot_equity, futures_equity, unrealized_pnl, net_deposits, timestamp 
            FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1
        ''')
        return self.cursor.fetchone()

    def get_equity_at_time(self, target_time_str):
        # Find the snapshot closest to the target time
        self.cursor.execute('''
            SELECT total_equity, spot_equity, futures_equity, unrealized_pnl, net_deposits, timestamp 
            FROM equity_snapshots 
            WHERE timestamp <= ? 
            ORDER BY timestamp DESC LIMIT 1
        ''', (target_time_str,))
        return self.cursor.fetchone()

    def get_trade_stats(self):
        self.cursor.execute('''
            SELECT COUNT(*), SUM(pnl), COUNT(CASE WHEN pnl > 0 THEN 1 END)
            FROM trade_history
        ''')
        row = self.cursor.fetchone()
        return {
            'total_trades': row[0] or 0,
            'total_pnl': row[1] or 0,
            'wins': row[2] or 0
        }

    def save_candles(self, symbol, candles):
        """candles: list of [ts, o, h, l, c, v]"""
        self.cursor.executemany('''
            INSERT OR IGNORE INTO candle_history (symbol, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', [(symbol, c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles])
        self.conn.commit()

    def get_guard_status(self, strategy_type='GLN_Q'):
        self.cursor.execute('SELECT is_enabled, consecutive_losses, disabled_until FROM gln_guard WHERE strategy_type = ?', (strategy_type,))
        row = self.cursor.fetchone()
        if not row:
             # Initialize if not exists
             try:
                 self.cursor.execute('INSERT INTO gln_guard (strategy_type) VALUES (?)', (strategy_type,))
                 self.conn.commit()
             except: pass
             return {'is_enabled': True, 'consecutive_losses': 0, 'disabled_until': None}
        
        # Check if disabled_until has passed
        disabled_until_str = row[2]
        is_enabled = bool(row[0])
        if not is_enabled and disabled_until_str:
            from datetime import datetime
            try:
                if datetime.now() > datetime.fromisoformat(disabled_until_str):
                    self.update_guard_status(strategy_type, is_enabled=True, reset_losses=True)
                    return {'is_enabled': True, 'consecutive_losses': 0, 'disabled_until': None}
            except: pass
        
        return {'is_enabled': is_enabled, 'consecutive_losses': row[1], 'disabled_until': row[2]}

    def update_guard_status(self, strategy_type='GLN_Q', is_enabled=None, reset_losses=False, increment_loss=False, disable_hours=None):
        status = self.get_guard_status(strategy_type)
        current_losses = status.get('consecutive_losses') or 0
        new_losses = 0 if reset_losses else (current_losses + 1 if increment_loss else current_losses)
        new_enabled = is_enabled if is_enabled is not None else status['is_enabled']
        
        disabled_until = None
        if disable_hours:
            from datetime import datetime, timedelta
            disabled_until = (datetime.now() + timedelta(hours=disable_hours)).isoformat()
            new_enabled = False

        self.cursor.execute('''
            UPDATE gln_guard 
            SET is_enabled = ?, consecutive_losses = ?, disabled_until = ?, last_update = CURRENT_TIMESTAMP
            WHERE strategy_type = ?
        ''', (1 if new_enabled else 0, new_losses, disabled_until, strategy_type))
        self.conn.commit()


