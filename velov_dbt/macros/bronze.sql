{#
  Lecture du bronze : réponses JSON GBFS brutes écrites par velov.ingest / velov.gbfs_replay.
  Chemins relatifs au dossier d'exécution de dbt (racine du dépôt), voir la variable bronze_dir.
  Les colonnes sont déclarées explicitement : un champ ajouté par la source est ignoré,
  un champ attendu qui change de type fait échouer la lecture (visible, pas silencieux).
  Prérequis : au moins un fichier par flux (python -m velov.ingest ou python -m velov.gbfs_replay).
#}
{% macro bronze_station_status() %}
read_json('{{ var("bronze_dir") }}/station_status/*/*.json', columns = {
    last_updated: 'BIGINT',
    data: 'STRUCT(stations STRUCT(station_id VARCHAR, num_bikes_available INTEGER, num_docks_available INTEGER,
                                  is_installed BOOLEAN, is_renting BOOLEAN, is_returning BOOLEAN,
                                  last_reported BIGINT)[])'
})
{% endmacro %}

{% macro bronze_station_information() %}
read_json('{{ var("bronze_dir") }}/station_information/*/*.json', columns = {
    last_updated: 'BIGINT',
    data: 'STRUCT(stations STRUCT(station_id VARCHAR, capacity INTEGER)[])'
})
{% endmacro %}

{# Météo : format provisoire (source non confirmée), voir velov.gbfs_replay. #}
{% macro bronze_weather() %}
read_json('{{ var("bronze_dir") }}/weather/*/*.json', columns = {
    last_updated: 'BIGINT',
    data: 'STRUCT(temperature DOUBLE, is_raining BOOLEAN)'
})
{% endmacro %}
