-- Un instantané GBFS "station_status" déplié : une ligne par (station, instantané).
-- Typage et dédoublonnage ont lieu ici (bronze reste brut). Aucune règle métier.
with unnested as (
    select
        last_updated,
        unnest(data.stations) as s
    from {{ bronze_station_status() }}
)

select
    cast(s.station_id as integer) as station_id,
    to_timestamp(last_updated) as snapshot_at,
    to_timestamp(s.last_reported) as last_reported_at,
    s.num_bikes_available as bikes_available,
    s.num_docks_available as docks_available,
    s.is_installed,
    s.is_renting,
    s.is_returning
from unnested
where s.station_id is not null and last_updated is not null
qualify row_number() over (partition by cast(s.station_id as integer), last_updated order by s.last_reported desc) = 1
