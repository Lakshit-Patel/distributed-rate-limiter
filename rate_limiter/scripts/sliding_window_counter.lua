--[[
  Sliding Window Counter (Two-Window Weighted Approximation)
  
  KEYS[1] : Base key for Hash storing window counters (e.g., "rl:swc:user123")
  ARGV[1] : Limit
  ARGV[2] : Window size in seconds
  
  Why Redis TIME is used:
  The sliding window approximation calculates the dynamic weight:
    weight = (window - time_elapsed_in_current_window) / window
    estimated_requests = (previous_window_count * weight) + current_window_count
  If client machines supplied timestamps, differing local clocks would compute differing
  weights for the exact same millisecond, leading to unpredictable non-linear quota drops.
  Redis TIME guarantees mathematical consistency across all workers.
  
  Returns:
  { allowed (1 or 0), remaining, retry_after (seconds), reset_at (timestamp seconds) }
--]]

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

-- 1. Fetch server time
local time_arr = redis.call('TIME')
local now_sec = tonumber(time_arr[1])
local now_usec = tonumber(time_arr[2])
local now = now_sec + (now_usec / 1000000)

local current_window_bucket = math.floor(now_sec / window) * window
local previous_window_bucket = current_window_bucket - window

-- 2. Fetch stored hash data: curr_bucket, curr_count, prev_count
local data = redis.call('HMGET', key, 'bucket', 'curr', 'prev')
local stored_bucket = tonumber(data[1]) or 0
local curr_count = tonumber(data[2]) or 0
local prev_count = tonumber(data[3]) or 0

-- 3. Adjust state if time bucket has progressed
if stored_bucket == current_window_bucket then
    -- We are in the recorded current window
elseif stored_bucket == previous_window_bucket then
    -- Exactly one window has elapsed: old current becomes new previous
    prev_count = curr_count
    curr_count = 0
    stored_bucket = current_window_bucket
else
    -- More than one window elapsed: both windows reset
    prev_count = 0
    curr_count = 0
    stored_bucket = current_window_bucket
end

-- 4. Calculate weighted count
local time_into_current = now - current_window_bucket
local weight_previous = math.max(0, (window - time_into_current) / window)
local estimated_count = math.floor(prev_count * weight_previous) + curr_count

local allowed = 0
local remaining = 0
local retry_after = 0
local reset_at = current_window_bucket + window

if estimated_count < limit then
    curr_count = curr_count + 1
    redis.call('HMSET', key, 'bucket', stored_bucket, 'curr', curr_count, 'prev', prev_count)
    -- Expire after 2 full windows so previous count remains available for the full rollover
    redis.call('EXPIRE', key, window * 2 + 1)
    
    allowed = 1
    remaining = math.max(0, limit - (estimated_count + 1))
    retry_after = 0
else
    allowed = 0
    remaining = 0
    -- Time until weight decays enough to admit 1 request, or next bucket starts
    local wait_time = 0
    if prev_count > 0 then
        -- required weight such that math.floor(prev * w) + curr < limit
        -- wait_time occurs as time_into_current progresses
        wait_time = math.max(1, reset_at - now)
    else
        wait_time = math.max(1, reset_at - now)
    end
    retry_after = wait_time
end

return { allowed, remaining, tostring(retry_after), tostring(reset_at) }
