--[[
  Fixed Window Counter Rate Limiter
  
  KEYS[1] : Rate limit tracking key, formatted with bucket epoch (e.g. "rl:fw:user123:1712345600")
  ARGV[1] : Limit (maximum requests permitted within this fixed window)
  ARGV[2] : Window size in seconds
  
  Why Redis TIME is used:
  Multiple application workers distributed across different servers or containers
  may experience NTP clock drift (tens to hundreds of milliseconds). If client clocks
  computed the window start or expiry, different nodes would place requests into
  different windows or expire keys prematurely. Redis TIME guarantees that the single
  source of truth defines the timeline monotonically.
  
  Returns:
  { allowed (1 or 0), remaining, retry_after (seconds), reset_at (timestamp seconds) }
--]]

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

-- 1. Fetch Redis server authoritative time
local time_arr = redis.call('TIME')
local now_sec = tonumber(time_arr[1])
local now_usec = tonumber(time_arr[2])
local now = now_sec + (now_usec / 1000000)

-- 2. Increment the counter for this window
local current = redis.call('INCR', key)

-- 3. If new key, set expiration with safety buffer to avoid leaking memory
if current == 1 then
    redis.call('EXPIRE', key, window + 1)
end

-- 4. Calculate window boundary and reset epoch
local window_start = math.floor(now_sec / window) * window
local reset_at = window_start + window
local retry_after = 0

local allowed = 0
local remaining = 0

if current <= limit then
    allowed = 1
    remaining = limit - current
    retry_after = 0
else
    allowed = 0
    remaining = 0
    retry_after = math.max(0, reset_at - now)
end

return { allowed, remaining, tostring(retry_after), tostring(reset_at) }
