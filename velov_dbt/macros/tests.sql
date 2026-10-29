{#
  Tests génériques du projet.

  bikes_within_capacity : 0 <= bikes_available <= capacity. Renvoie la ligne de statistiques si la part de
  lignes hors bornes dépasse `max_violation_ratio` (0 = aucune tolérance). Avec deux instances (une en
  warn à 0, une en error au seuil du projet), on voit les anomalies avant qu'elles ne bloquent.

  unique_combination : unicité d'un tuple de colonnes (pas de dbt_utils dans ce projet : zéro dépendance réseau).
#}
{% test bikes_within_capacity(model, max_violation_ratio=0) %}
with stats as (
    select
        count(*) as n_rows,
        count(*) filter (where bikes_available < 0 or bikes_available > capacity) as n_violations
    from {{ model }}
)

select * from stats where n_violations > n_rows * {{ max_violation_ratio }}
{% endtest %}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
