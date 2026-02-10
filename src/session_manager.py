#!/usr/bin/env python3
"""
Session Manager - handles Telegram session file rotation, backup, and recovery.
Detects corrupted sessions and automatically creates new ones with re-auth.
"""

import os
import shutil
import time
from pathlib import Path
from datetime import datetime


class SessionManager:
    """Manages Telegram session files with automatic rotation and recovery"""
    
    def __init__(self, base_session_name: str = "trading_bot_session", sessions_dir: str = "sessions"):
        self.base_name = base_session_name
        self.sessions_dir = sessions_dir
        self.current_session_file = self.base_name
        
        # Ensure sessions directory exists
        os.makedirs(sessions_dir, exist_ok=True)
    
    def get_current_session_file(self) -> str:
        """Return the current session file path"""
        return self.current_session_file
    
    def backup_session(self) -> Optional[str]:
        """
        Backup current session file with timestamp.
        Returns backup file path or None if no session exists.
        """
        if not os.path.exists(self.current_session_file):
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.base_name}.backup_{timestamp}"
        backup_path = os.path.join(self.sessions_dir, backup_name)
        
        try:
            shutil.copy2(self.current_session_file, backup_path)
            print(f"[SESSION] 💾 Backed up session to {backup_path}")
            return backup_path
        except Exception as e:
            print(f"[SESSION] ⚠️ Failed to backup session: {e}")
            return None
    
    def rotate_session(self) -> bool:
        """
        Rotate to a new session file.
        This forces the client to re-authenticate on next start.
        Useful for recovering from corrupted session files.
        """
        # Backup the old session first
        backup_path = self.backup_session()
        
        # Move current session to archive
        if os.path.exists(self.current_session_file):
            try:
                archive_name = f"{self.base_name}.old_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                archive_path = os.path.join(self.sessions_dir, archive_name)
                os.rename(self.current_session_file, archive_path)
                print(f"[SESSION] 🔄 Archived old session to {archive_path}")
            except Exception as e:
                print(f"[SESSION] ⚠️ Failed to archive old session: {e}")
        
        print(f"[SESSION] ✅ Session rotated - will re-authenticate on next start")
        return True
    
    def should_rotate(self, error_type: str = "corruption", error_count: int = 3) -> bool:
        """
        Determine if session should be rotated based on error context.
        error_type: "corruption", "tlnotfound", "auth", etc.
        error_count: number of consecutive errors of this type
        
        Returns True if rotation is recommended.
        """
        if error_type == "corruption":
            # Any corruption error warrants immediate rotation
            return True
        elif error_type == "tlnotfound":
            # TLObject errors might be transient, but rotate after 3+ in a row
            return error_count >= 3
        elif error_type == "auth":
            # Auth errors should trigger rotation
            return True
        
        return False
    
    def cleanup_old_backups(self, keep_count: int = 5):
        """
        Clean up old backup files, keeping only the most recent K backups.
        Helps prevent disk space issues.
        """
        backup_files = sorted([
            f for f in os.listdir(self.sessions_dir)
            if f.startswith(f"{self.base_name}.backup_")
        ])
        
        if len(backup_files) > keep_count:
            to_remove = backup_files[:-keep_count]
            for f in to_remove:
                try:
                    os.remove(os.path.join(self.sessions_dir, f))
                    print(f"[SESSION] 🗑️ Removed old backup {f}")
                except Exception as e:
                    print(f"[SESSION] ⚠️ Failed to remove {f}: {e}")
    
    def list_sessions(self) -> list:
        """List all session files in the sessions directory"""
        if not os.path.exists(self.sessions_dir):
            return []
        
        files = []
        for f in os.listdir(self.sessions_dir):
            if self.base_name in f:
                full_path = os.path.join(self.sessions_dir, f)
                size = os.path.getsize(full_path)
                files.append({"name": f, "path": full_path, "size_kb": size / 1024})
        
        return sorted(files, key=lambda x: x['name'], reverse=True)
    
    def get_session_age_seconds(self) -> int:
        """Get age of current session file in seconds"""
        if not os.path.exists(self.current_session_file):
            return -1
        
        mtime = os.path.getmtime(self.current_session_file)
        return int(time.time() - mtime)
