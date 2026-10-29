-- Description des stations (capacité) : une ligne par (station, instantané).
with unnested as (
    select
        last_updated,
        unnest(data.stations) as s
    from {{ bronze_station_information() }}
)

select
    cast(s.station_id as integer) as station_id,
    to_timestamp(last_updated) as snapshot_at,
    s.capacity
from unnested
where s.station_id is not null and last_updated is not null
qualify row_number() over (partition by cast(s.station_id as integer), last_updated order by s.capacity desc) = 1
