from __future__ import annotations
import secrets
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class AuthRepositoryMixin:
    def get_password_status(self, user_id: int) -> dict:
        from datetime import datetime, timezone

        sql = """
        SELECT password_changed_at
        FROM kirana_oltp.users
        WHERE user_id = :uid AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        if not row:
            return {"has_password": False, "last_changed_at": None, "can_change": True}
        last_changed = row["password_changed_at"]
        has_password = last_changed is not None
        can_change = True
        days_left = 0
        if last_changed:
            last_changed_utc = (
                last_changed.replace(tzinfo=timezone.utc)
                if last_changed.tzinfo is None
                else last_changed.astimezone(timezone.utc)
            )
            days_since = (datetime.now(timezone.utc) - last_changed_utc).days
            can_change = days_since >= 14
            days_left = max(0, 14 - days_since)
        return {
            "has_password": has_password,
            "last_changed_at": last_changed.isoformat() if last_changed else None,
            "can_change": can_change,
            "days_until_change": days_left,
        }

    def change_password(
        self, user_id: int, old_password: str | None, new_password: str
    ) -> None:
        from datetime import datetime, timezone

        sql = """
        SELECT password_hash, password_salt, password_changed_at
        FROM kirana_oltp.users
        WHERE user_id = :uid AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        if not row:
            raise ValueError("User not found")
        has_password = row["password_changed_at"] is not None
        # Cooldown check
        if row["password_changed_at"]:
            last_changed = row["password_changed_at"]
            last_changed_utc = (
                last_changed.replace(tzinfo=timezone.utc)
                if last_changed.tzinfo is None
                else last_changed.astimezone(timezone.utc)
            )
            days_since = (datetime.now(timezone.utc) - last_changed_utc).days
            if days_since < 14:
                days_left = 14 - days_since
                raise ValueError(
                    f"Password can only be changed once every 14 days. Try again in {days_left} day(s)."
                )
        # Verify old password when user already has one
        if has_password:
            if not old_password:
                raise ValueError("Current password is required")
            if not secrets.compare_digest(
                self._hash(old_password, row["password_salt"] or ""),
                row["password_hash"] or "",
            ):
                raise ValueError("Current password is incorrect")
        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters")
        salt = secrets.token_hex(16)
        ph = self._hash(new_password, salt)
        with self._conn() as conn:
            conn.execute(
                text("""
            UPDATE kirana_oltp.users
            SET password_hash = :ph, password_salt = :salt, password_changed_at = NOW()
            WHERE user_id = :uid
            """),
                {"ph": ph, "salt": salt, "uid": user_id},
            )
            conn.commit()

    def authenticate_user(self, username: str, password: str) -> dict | None:
        sql = """
        SELECT user_id, username, full_name, role, store_id, password_salt, password_hash
        FROM kirana_oltp.users
        WHERE username = :u AND is_active = TRUE AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"u": username}).mappings().first()
        if not row:
            return None
        if not secrets.compare_digest(
            self._hash(password, row["password_salt"] or ""), row["password_hash"] or ""
        ):
            return None
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": row["role"],
            "store_id": row["store_id"],
        }

    def authenticate_by_phone(
        self, phone_number: str, firebase_uid: str | None = None
    ) -> dict | None:
        """Look up an active user by phone number or firebase_uid (Firebase already verified the OTP).

        If the user is found by phone number but their stored firebase_uid differs from the one
        provided (e.g. after switching Firebase projects or migrating to a new environment),
        the stored UID is silently updated so future lookups stay consistent.
        """
        sql = """
        SELECT user_id, username, full_name, role, store_id, firebase_uid AS stored_fuid
        FROM kirana_oltp.users
        WHERE (phone_number = :phone OR (:fuid IS NOT NULL AND firebase_uid = :fuid))
          AND is_active = TRUE AND COALESCE(is_deleted, FALSE) = FALSE
        LIMIT 1
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"phone": phone_number, "fuid": firebase_uid})
                .mappings()
                .first()
            )
            if not row:
                return None
            user = dict(row)
            # Heal mismatched firebase_uid (different Firebase project, environment migration, etc.)
            if firebase_uid and user.get("stored_fuid") != firebase_uid:
                conn.execute(
                    text(
                        "UPDATE kirana_oltp.users SET firebase_uid = :fuid WHERE user_id = :uid"
                    ),
                    {"fuid": firebase_uid, "uid": user["user_id"]},
                )
                conn.commit()
                logger.info("firebase_uid healed for user_id=%s", user["user_id"])
        return {
            k: user[k] for k in ("user_id", "username", "full_name", "role", "store_id")
        }

    def check_username_available(self, username: str) -> bool:
        sql = "SELECT 1 FROM kirana_oltp.users WHERE LOWER(username) = LOWER(:u)"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"u": username}).first()
        return row is None

    def create_session(
        self,
        user_id: int,
        login_method: str = "password",
        telemetry: dict | None = None,
    ) -> str:
        token = secrets.token_hex(32)
        telemetry = telemetry or {}
        sql = """
            INSERT INTO kirana_oltp.user_sessions(
                user_id, access_token, created_at, login_method,
                device_brand, device_model, os_name, os_version, ip_address
            )
            VALUES(:uid, :tok, NOW(), :method, :brand, :model, :os, :osv, :ip)
        """
        with self._conn() as conn:
            conn.execute(
                text(sql),
                {
                    "uid": user_id,
                    "tok": token,
                    "method": login_method,
                    "brand": telemetry.get("device_brand"),
                    "model": telemetry.get("device_model"),
                    "os": telemetry.get("os_name"),
                    "osv": telemetry.get("os_version"),
                    "ip": telemetry.get("ip_address"),
                },
            )
            conn.commit()
        return token

    def list_active_sessions(self, limit: int = 100) -> list[dict]:
        sql = """
            SELECT s.*, u.username, u.full_name, st.name AS store_name
            FROM kirana_oltp.user_sessions s
            JOIN kirana_oltp.users u ON s.user_id = u.user_id
            LEFT JOIN kirana_oltp.store st ON u.store_id = st.store_id
            WHERE s.revoked_at IS NULL
              AND s.created_at > NOW() - INTERVAL '30 days'
            ORDER BY s.created_at DESC
            LIMIT :lim
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"lim": limit}).mappings().all()
        return [dict(r) for r in rows]

    def get_user_by_token(self, token: str) -> dict | None:
        sql = """
        SELECT u.user_id, u.username, u.full_name, u.role, u.store_id
        FROM kirana_oltp.user_sessions s
        JOIN kirana_oltp.users u ON s.user_id = u.user_id
        WHERE s.access_token = :tok
          AND s.revoked_at IS NULL
          AND s.created_at > NOW() - INTERVAL '30 days'
          AND u.is_active = TRUE
          AND COALESCE(u.is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"tok": token}).mappings().first()
        return dict(row) if row else None

    def create_user(
        self,
        username: str,
        password: str,
        full_name: str,
        role: str,
        store_id: int | None,
    ) -> dict:
        salt = secrets.token_hex(16)
        ph = self._hash(password, salt)
        sql = """
        INSERT INTO kirana_oltp.users
            (username, email, full_name, role, store_id, password_salt, password_hash, is_active)
        VALUES(:u, :email, :fn, :r, :sid, :salt, :ph, TRUE)
        RETURNING user_id, username, full_name, role, store_id
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "u": username,
                        "email": self._default_email(username),
                        "fn": full_name,
                        "r": role,
                        "sid": store_id,
                        "salt": salt,
                        "ph": ph,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row)

    def get_user_by_username(self, username: str) -> dict | None:
        sql = """
        SELECT user_id, username, full_name, role, store_id, is_active
        FROM kirana_oltp.users
        WHERE username = :username AND is_active = TRUE
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"username": username}).mappings().first()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        sql = """
        SELECT user_id, username, full_name, role, store_id, is_active
        FROM kirana_oltp.users
        ORDER BY user_id
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]

    def delete_user(self, user_id: int) -> bool:
        sql = "UPDATE kirana_oltp.users SET is_active = FALSE WHERE user_id = :uid RETURNING user_id"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).first()
            conn.commit()
        return row is not None

    def update_user_profile(
        self, user_id: int, full_name: str | None, password: str | None
    ) -> dict | None:
        sets, params = [], {"uid": user_id}
        if full_name:
            sets.append("full_name = :fn")
            params["fn"] = full_name
        if password:
            salt = secrets.token_hex(16)
            params.update({"salt": salt, "ph": self._hash(password, salt)})
            sets += ["password_salt = :salt", "password_hash = :ph"]
        if not sets:
            return None
        sql = (
            f"UPDATE kirana_oltp.users SET {', '.join(sets)} WHERE user_id = :uid "
            f"RETURNING user_id, username, full_name, role, store_id"
        )
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        return dict(row) if row else None
