-- Garde-fou contre la perte silencieuse de lignes (jointure météo manquante, capacité inconnue, valeurs écartées).
-- Échoue si gold est vide, ou s'il garde moins de (1 - max_dropped_ratio) des candidats station x heure.
{{ config(severity='error') }}
with counts as (
    select
        (select count(*) from {{ ref('silver_station_hourly') }}) as candidates,
        (select count(*) from {{ ref('gold_velov_hourly') }}) as published
)

select *
from counts
where published = 0
   or published < candidates * (1 - {{ var('max_dropped_ratio') }})
