{
  // Datasource UIDs — соответствуют Grafana provisioning (existing prometheus + loki).
  ds:: {
    prom: 'prometheus',
    loki: 'loki',
  },

  // Folder для всех YUSIC дашбордов в Grafana
  folder:: 'YUSIC',

  // Tags для tag-based навигации
  tags:: {
    fleet: ['fleet', 'yusic'],
    app: ['yusic-app', 'yusic'],
    crossproject: ['cross-project', 'yusic'],
    logs: ['logs', 'yusic'],
  },

  // Стандартный refresh + time range
  refresh:: '30s',
  timeFrom:: 'now-1h',
  timeTo:: 'now',
}
