"""Immutable learned profiles and serialized append-only activation pointers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from prme.models.learning import (
    RankingProfile,
    RankingProfileApplication,
    RankingProfileState,
)
from prme.storage._threading import run_to_completion
from prme.storage.relevance import RelevanceRepository
from prme.types import Scope


class StaleRankingProfileError(ValueError):
    """The active profile changed after a caller selected its transition."""


def profile_operation_id(user_id: str, profile_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([
        "prme:ranking-profile:v1", user_id, str(profile_id),
    ])))


def state_operation_id(user_id: str, change_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([
        "prme:ranking-profile-state:v1", user_id, str(change_id),
    ])))


def scope_key(scopes: tuple[Scope, ...] | None) -> str:
    values = [scope.value for scope in scopes] if scopes is not None else None
    return "ranking:" + hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode()
    ).hexdigest()


def _payload(record) -> str:
    raw = record.model_dump_json()
    return json.dumps({"record": raw, "checksum": hashlib.sha256(raw.encode()).hexdigest()})


def _unpack(value, model, *, user_id: str):
    value = json.loads(value) if isinstance(value, str) else value
    raw = value["record"]
    if hashlib.sha256(raw.encode()).hexdigest() != value.get("checksum"):
        raise ValueError("Ranking profile record checksum mismatch")
    record = model.model_validate_json(raw)
    if record.user_id != user_id or record.checksum != value["checksum"]:
        raise ValueError("Ranking profile record owner or canonical checksum mismatch")
    return record


def _changed_at_after(previous: RankingProfileState | None) -> datetime:
    """Keep pointer ordering monotonic even if the wall clock moves backward."""
    changed_at = datetime.now(timezone.utc)
    if previous is not None and changed_at <= previous.changed_at:
        return previous.changed_at + timedelta(microseconds=1)
    return changed_at


class RankingProfileRepository(RelevanceRepository):
    @staticmethod
    def _read_profile(value, *, user_id: str, profile_id: UUID | None = None) -> RankingProfile:
        profile = _unpack(value, RankingProfile, user_id=user_id)
        if profile_id is not None and profile.profile_id != profile_id:
            raise ValueError("Ranking profile identity mismatch")
        return profile

    @staticmethod
    def _read_state(value, *, user_id: str, change_id: UUID | None = None) -> RankingProfileState:
        state = _unpack(value, RankingProfileState, user_id=user_id)
        if change_id is not None and state.change_id != change_id:
            raise ValueError("Ranking profile state identity mismatch")
        return state

    async def get(self, profile_id: str, *, user_id: str) -> RankingProfile | None:
        self._owner(user_id)
        identity = UUID(profile_id)
        rows = await self._query(
            "SELECT payload FROM operations WHERE id=$1 AND actor_id=$2 "
            "AND op_type='RANKING_PROFILE_CREATED'",
            profile_operation_id(user_id, identity), user_id,
        )
        return self._read_profile(
            rows[0]["payload"], user_id=user_id, profile_id=identity,
        ) if rows else None

    async def create(self, profile: RankingProfile) -> RankingProfile:
        profile = RankingProfile.model_validate_json(profile.model_dump_json())
        await self._query(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES ($1,'RANKING_PROFILE_CREATED',$2,$3,$4,$5,$6) ON CONFLICT (id) DO NOTHING",
            profile_operation_id(profile.user_id, profile.profile_id), str(profile.profile_id),
            _payload(profile), profile.user_id, scope_key(profile.scopes), profile.created_at,
        )
        saved = await self.get(str(profile.profile_id), user_id=profile.user_id)
        if saved is None or not saved.matches_evidence(profile):
            raise ValueError("profile_id already identifies a different ranking profile")
        return saved

    async def get_change(self, change_id: str, *, user_id: str) -> RankingProfileState | None:
        self._owner(user_id)
        identity = UUID(change_id)
        rows = await self._query(
            "SELECT payload FROM operations WHERE id=$1 AND actor_id=$2 "
            "AND op_type='RANKING_PROFILE_STATE_CHANGED'",
            state_operation_id(user_id, identity), user_id,
        )
        return self._read_state(
            rows[0]["payload"], user_id=user_id, change_id=identity,
        ) if rows else None

    async def list(self, *, user_id: str, limit: int = 100,
                   after_id: str | None = None) -> list[RankingProfile]:
        self._owner(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        after = str(UUID(after_id)) if after_id is not None else ""
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type='RANKING_PROFILE_CREATED' "
            "AND actor_id=$1 AND target_id>$2 ORDER BY target_id LIMIT $3",
            user_id, after, limit,
        )
        return [self._read_profile(row["payload"], user_id=user_id) for row in rows]

    async def latest_state(self, *, user_id: str,
                           scopes: tuple[Scope, ...] | None) -> RankingProfileState | None:
        self._owner(user_id)
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type='RANKING_PROFILE_STATE_CHANGED' "
            "AND actor_id=$1 AND namespace_id=$2 ORDER BY created_at DESC,id DESC LIMIT 1",
            user_id, scope_key(scopes),
        )
        state = self._read_state(rows[0]["payload"], user_id=user_id) if rows else None
        if state is not None and state.scopes != scopes:
            raise ValueError("Ranking profile state scope mismatch")
        return state

    async def active(self, *, user_id: str,
                     scopes: tuple[Scope, ...] | None) -> RankingProfile | None:
        state = await self.latest_state(user_id=user_id, scopes=scopes)
        if state is None or state.active_profile_id is None:
            return None
        profile = await self.get(str(state.active_profile_id), user_id=user_id)
        if profile is None or profile.scopes != scopes:
            raise ValueError("Active ranking profile is missing or has different scopes")
        return profile

    async def active_application(
        self, *, user_id: str, scopes: tuple[Scope, ...] | None,
    ) -> RankingProfileApplication | None:
        state = await self.latest_state(user_id=user_id, scopes=scopes)
        if state is None or state.active_profile_id is None:
            return None
        if state.application is not None:
            return state.application
        profile = await self.get(str(state.active_profile_id), user_id=user_id)
        if profile is None or profile.scopes != scopes:
            raise ValueError("Active ranking profile is missing or has different scopes")
        return profile.application

    async def history(self, *, user_id: str, scopes: tuple[Scope, ...] | None,
                      limit: int = 100) -> list[RankingProfileState]:
        self._owner(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        rows = await self._query(
            "SELECT payload FROM operations WHERE op_type='RANKING_PROFILE_STATE_CHANGED' "
            "AND actor_id=$1 AND namespace_id=$2 ORDER BY created_at DESC,id DESC LIMIT $3",
            user_id, scope_key(scopes), limit,
        )
        states = [self._read_state(row["payload"], user_id=user_id) for row in rows]
        if any(state.scopes != scopes for state in states):
            raise ValueError("Ranking profile state scope mismatch")
        return states

    async def change(
        self, *, user_id: str, scopes: tuple[Scope, ...] | None,
        application: RankingProfileApplication | None, expected_profile_id: UUID | None,
        action: str, change_id: UUID,
    ) -> RankingProfileState:
        self._owner(user_id)
        if self.pool is not None:
            return await self._change_pg(
                user_id=user_id, scopes=scopes, application=application,
                expected_profile_id=expected_profile_id, action=action, change_id=change_id,
            )
        async with self.lock:
            return await run_to_completion(
                self._change_duck,
                user_id, scopes, application, expected_profile_id, action, change_id,
            )

    def _change_duck(self, user_id, scopes, application, expected_profile_id,
                     action, change_id):
        active_profile_id = application.profile_id if application is not None else None
        operation_id = state_operation_id(user_id, change_id)
        self.conn.execute("BEGIN TRANSACTION")
        try:
            existing = self.conn.execute(
                "SELECT payload FROM operations WHERE id=? AND actor_id=? "
                "AND op_type='RANKING_PROFILE_STATE_CHANGED'", [operation_id, user_id],
            ).fetchone()
            if existing:
                state = self._read_state(existing[0], user_id=user_id, change_id=change_id)
                requested = (scopes, active_profile_id, action)
                if (state.scopes, state.active_profile_id, state.action) != requested:
                    raise ValueError("change_id already identifies a different ranking profile transition")
                self.conn.execute("COMMIT")
                return state
            row = self.conn.execute(
                "SELECT payload FROM operations WHERE op_type='RANKING_PROFILE_STATE_CHANGED' "
                "AND actor_id=? AND namespace_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                [user_id, scope_key(scopes)],
            ).fetchone()
            previous_state = self._read_state(row[0], user_id=user_id) if row else None
            previous = previous_state.active_profile_id if previous_state else None
            if previous != expected_profile_id:
                raise StaleRankingProfileError("Active ranking profile changed; inspect and retry")
            state = RankingProfileState(
                change_id=change_id, user_id=user_id, scopes=scopes, action=action,
                previous_profile_id=previous, active_profile_id=active_profile_id,
                application=application,
                changed_at=_changed_at_after(previous_state),
            )
            self.conn.execute(
                "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                "VALUES (?,'RANKING_PROFILE_STATE_CHANGED',?,?,?,?,?)",
                [operation_id, str(active_profile_id) if active_profile_id else None,
                 _payload(state), user_id, scope_key(scopes), state.changed_at],
            )
            self.conn.execute("COMMIT")
            return state
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    async def _change_pg(self, *, user_id, scopes, application,
                         expected_profile_id, action, change_id):
        active_profile_id = application.profile_id if application is not None else None
        operation_id = state_operation_id(user_id, change_id)
        lock_value = int.from_bytes(hashlib.sha256(
            f"{user_id}:{scope_key(scopes)}".encode()
        ).digest()[:8], "big", signed=True)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_value)
            existing = await conn.fetchrow(
                "SELECT payload FROM operations WHERE id=$1 AND actor_id=$2 "
                "AND op_type='RANKING_PROFILE_STATE_CHANGED'", operation_id, user_id,
            )
            if existing:
                state = self._read_state(existing["payload"], user_id=user_id, change_id=change_id)
                if (state.scopes, state.active_profile_id, state.action) != (
                    scopes, active_profile_id, action,
                ):
                    raise ValueError("change_id already identifies a different ranking profile transition")
                return state
            row = await conn.fetchrow(
                "SELECT payload FROM operations WHERE op_type='RANKING_PROFILE_STATE_CHANGED' "
                "AND actor_id=$1 AND namespace_id=$2 ORDER BY created_at DESC,id DESC LIMIT 1",
                user_id, scope_key(scopes),
            )
            previous_state = self._read_state(row["payload"], user_id=user_id) if row else None
            previous = previous_state.active_profile_id if previous_state else None
            if previous != expected_profile_id:
                raise StaleRankingProfileError("Active ranking profile changed; inspect and retry")
            state = RankingProfileState(
                change_id=change_id, user_id=user_id, scopes=scopes, action=action,
                previous_profile_id=previous, active_profile_id=active_profile_id,
                application=application,
                changed_at=_changed_at_after(previous_state),
            )
            await conn.execute(
                "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                "VALUES ($1,'RANKING_PROFILE_STATE_CHANGED',$2,$3::jsonb,$4,$5,$6)",
                operation_id, str(active_profile_id) if active_profile_id else None,
                _payload(state), user_id, scope_key(scopes), state.changed_at,
            )
            return state
