--[[
  Sliding Window Log (Sorted Set) Rate Limiter
  
  KEYS[1] : Key for Sorted Set storing timestamps of admitted requests
  ARGV[1] : Limit (maximum permitted requests in window)
  ARGV[2] : Window size in seconds
  
  Why Redis TIME is used:
  In a sliding window log, every individual request timestamp is stored as a member score
  in a Redis Sorted Set. If client machines pushed their local timestamps, a client with
  a clock skewed 2 seconds forward would pollute the log with future timestamps, throwing off
  the sliding boundary. Redis TIME ensures all request scores are strictly aligned with
  the Redis engine's single monotonic clock.
  
  Returns:
  { allowed (1 or 0), remaining, retry_after (seconds), reset_at (timestamp seconds) }
--]]

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

-- 1. Get Redis server authoritative time
local time_arr = redis.call('TIME')
local now_sec = tonumber(time_arr[1])
local now_usec = tonumber(time_arr[2])
local now = now_sec + (now_usec / 1000000)

local window_start = now - window

-- 2. Evict timestamps strictly older than the sliding window start
redis.call('ZREMRANGEBYSCORE', key, '-inf', '(' .. tostring(window_start))

-- 3. Count remaining requests within current sliding window
local count = redis.call('ZCARD', key)

local allowed = 0
local remaining = 0
local retry_after = 0
local reset_at = now + window

if count < limit then
    -- Unique member: combine microsecond timestamp with incrementing sequence or unique tag
    local member = tostring(now) .. '-' .. tostring(count + 1)
    redis.call('ZADD', key, now, member)
    -- Refresh TTL: key automatically expires if dormant for window + 1 seconds
    redis.call('EXPIRE', key, window + 1)
    
    allowed = 1
    remaining = limit - (count + 1)
    retry_after = 0
else
    allowed = 0
    remaining = 0
    -- The oldest event in the current window dictates when the first slot will open up
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest and #oldest >= 2 then
        local oldest_ts = tonumber(oldest[2])
        local slot_freed_at = oldest_ts + window
        retry_after = math.max(0, slot_freed_at - now)
        reset_at = slot_freed_at
    else
        retry_after = window
        reset_at = now + window
    end
end

return { allowed, remaining, tostring(retry_after), tostring(reset_at) }
