--[[
  Token Bucket Rate Limiter
  
  KEYS[1] : Key for Hash storing token balance and last refresh timestamp
  ARGV[1] : Capacity (maximum burst tokens in bucket)
  ARGV[2] : Window size in seconds (refill rate = capacity / window tokens per second)
  ARGV[3] : Requested tokens (defaults to 1)
  
  Why Redis TIME is used:
  Token replenishment formula:
    tokens_to_add = (now - last_refreshed) * refill_rate
  If multiple distributed workers relied on local client system times, any machine
  with a slightly advanced clock would calculate massive artificial token additions,
  refilling the bucket prematurely and allowing forbidden request spikes. Redis TIME
  guarantees that token accumulation depends exclusively on the central Redis engine clock.
  
  Returns:
  { allowed (1 or 0), remaining, retry_after (seconds), reset_at (timestamp seconds) }
--]]

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local requested = tonumber(ARGV[3]) or 1

-- 1. Authoritative Redis time
local time_arr = redis.call('TIME')
local now_sec = tonumber(time_arr[1])
local now_usec = tonumber(time_arr[2])
local now = now_sec + (now_usec / 1000000)

local refill_rate = capacity / window -- tokens per second

-- 2. Fetch bucket state
local data = redis.call('HMGET', key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

if not tokens or not last_updated then
    -- Initial bucket setup: full capacity, updated now
    tokens = capacity
    last_updated = now
else
    -- Replenish tokens based on elapsed server time
    local delta = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + (delta * refill_rate))
    last_updated = now
end

local allowed = 0
local remaining = 0
local retry_after = 0
local reset_at = now

if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
    remaining = math.floor(tokens)
    retry_after = 0
    -- Time until bucket is completely full again
    reset_at = now + ((capacity - tokens) / refill_rate)
else
    allowed = 0
    remaining = math.floor(tokens)
    -- Seconds needed until enough tokens are available to satisfy requested amount
    local needed = requested - tokens
    retry_after = math.max(0.001, needed / refill_rate)
    reset_at = now + ((capacity - tokens) / refill_rate)
end

-- 3. Save new state and set TTL (time to refill completely + buffer)
redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(last_updated))
local ttl = math.ceil(window * 2) + 1
redis.call('EXPIRE', key, ttl)

return { allowed, remaining, tostring(retry_after), tostring(reset_at) }
