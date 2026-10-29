-- Table gold au schéma du data contract (une ligne par station et par heure UTC) :
--   station_id, timestamp, capacity, bikes_available, temperature, is_raining
--
-- EX-11 : l'instantané retenu pour l'heure H n'est jamais postérieur à H (voir silver_station_hourly),
-- la capacité est celle connue à cet instant (asof join), la météo est celle de l'heure H.
-- EX-10 : table reconstruite à l'identique depuis le bronze (aucun now(), aucun état).
-- Les valeurs impossibles (vélos négatifs ou supérieurs à la capacité) sont écartées ici ; le test
-- gold_keeps_enough_rows alerte si cet écart, ou une jointure, fait perdre trop de lignes.
select
    cast(h.station_id as integer) as station_id,
    h.hour_at as "timestamp",
    cast(i.capacity as integer) as capacity,
    cast(h.bikes_available as integer) as bikes_available,
    cast(w.temperature as double) as temperature,
    cast(w.is_raining as boolean) as is_raining
from {{ ref('silver_station_hourly') }} as h
-- capacité connue à l'instant de l'observation (jamais plus tard)
asof join {{ ref('silver_station_information') }} as i
    on h.station_id = i.station_id and h.snapshot_at >= i.snapshot_at
-- météo de l'heure H
inner join {{ ref('silver_weather') }} as w on w.observed_at = h.hour_at
where i.capacity > 0
  and h.bikes_available between 0 and i.capacity
