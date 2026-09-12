"""Short-lived operational observations; Redis never owns business task state."""

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry


def redis_client(url):
    return redis.Redis.from_url(
        url, socket_connect_timeout=0.5, socket_timeout=0.5, retry=Retry(NoBackoff(), 0), decode_responses=True
    )


_PULSE_WORKER = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 90)
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('EXPIRE', KEYS[1], 90)
return 1
"""
_PULSE_BEAT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('SET', KEYS[1], tostring(now), 'EX', 90)
return 1
"""
_SNAPSHOT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local count = redis.call('ZCOUNT', KEYS[1], now - 30, '+inf')
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local age = -1
if #oldest > 0 then age = math.max(0, now - tonumber(oldest[2])) end
local beat = redis.call('GET', KEYS[2])
local beat_age = -1
if beat then beat_age = math.max(0, now - tonumber(beat)) end
return {count, tostring(age), tostring(beat_age)}
"""


class OperationsHeartbeats:
    def __init__(self, url: str, *, namespace: str = "recruitmatch:operations"):
        self.url = url
        self.worker_key = namespace + ":workers"
        self.beat_key = namespace + ":beat"

    def pulse_worker(self, process_id: str) -> None:
        with redis_client(self.url) as client:
            client.eval(_PULSE_WORKER, 1, self.worker_key, process_id)

    def remove_worker(self, process_id: str) -> None:
        with redis_client(self.url) as client:
            client.zrem(self.worker_key, process_id)

    def pulse_beat(self) -> None:
        with redis_client(self.url) as client:
            client.eval(_PULSE_BEAT, 1, self.beat_key)

    def snapshot(self) -> dict:
        try:
            with redis_client(self.url) as client:
                count, age, beat_age = client.eval(_SNAPSHOT, 2, self.worker_key, self.beat_key)
            age, beat_age = float(age), float(beat_age)
            return {
                "worker": "fresh" if count else "stale",
                "worker_count": count,
                "worker_oldest_heartbeat_age_seconds": round(age, 3) if age >= 0 else None,
                "beat": "fresh" if 0 <= beat_age <= 30 else "stale",
                "beat_heartbeat_age_seconds": round(beat_age, 3) if beat_age >= 0 else None,
            }
        except redis.RedisError:
            return {
                "worker": "unknown",
                "worker_count": None,
                "worker_oldest_heartbeat_age_seconds": None,
                "beat": "unknown",
                "beat_heartbeat_age_seconds": None,
            }
