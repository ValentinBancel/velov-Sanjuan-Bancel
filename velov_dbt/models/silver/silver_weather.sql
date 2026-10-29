-- Météo (format provisoire) : une ligne par instant de relevé.
select
    to_timestamp(last_updated) as observed_at,
    data.temperature as temperature,
    data.is_raining as is_raining
from {{ bronze_weather() }}
where last_updated is not null
qualify row_number() over (partition by last_updated order by data.temperature desc) = 1
