-- Candidats de la table gold : pour chaque (station, heure H), le dernier instantané pris AU PLUS TARD à H
-- et au plus {{ var('max_lag_minutes') }} min avant (EX-11 : jamais d'information postérieure à l'instant de prédiction).
-- Jamais de moyenne sur l'heure ni d'instantané postérieur à H.
with snapshots as (
    select
        *,
        -- heure ronde H >= snapshot_at (un instantané à 13:00:00 pile vaut H = 13:00)
        date_trunc('hour', snapshot_at - interval 1 microsecond) + interval 1 hour as hour_at
    from {{ ref('silver_station_status') }}
    where is_installed
)

select *
from snapshots
where hour_at - snapshot_at < interval '{{ var("max_lag_minutes") }} minutes'
qualify row_number() over (partition by station_id, hour_at order by snapshot_at desc) = 1
