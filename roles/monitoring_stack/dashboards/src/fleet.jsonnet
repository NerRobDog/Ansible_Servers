local g = import 'grafana.libsonnet';
local yusic = import 'lib/yusic.libsonnet';

local prom = { type: 'prometheus', uid: yusic.ds.prom };

local panel(title, expr, legend, unit, gridPos) = g.graphPanel.new(
  title=title,
  datasource=prom,
).addTarget(
  g.prometheus.target(expr=expr, legendFormat=legend)
) + {
  fieldConfig: { defaults: { unit: unit } },
  gridPos: gridPos,
};

local statPanel(title, expr, legend, unit, gridPos) = g.statPanel.new(
  title=title,
  datasource=prom,
).addTarget(
  g.prometheus.target(expr=expr, legendFormat=legend)
) + {
  fieldConfig: { defaults: { unit: unit } },
  gridPos: gridPos,
};

g.dashboard.new(
  title='YUSIC — Fleet Overview',
  uid='yusic-fleet-overview',
  tags=yusic.tags.fleet,
  refresh=yusic.refresh,
  time_from=yusic.timeFrom,
  time_to=yusic.timeTo,
  schemaVersion=39,
).addPanels([
  statPanel(
    title='Hosts up',
    expr='count(up{job=~"monitoring-(node-exporter|cadvisor)"} == 1)',
    legend='up',
    unit='short',
    gridPos={ x: 0, y: 0, w: 6, h: 4 },
  ),
  statPanel(
    title='Hosts down',
    expr='count(up{job=~"monitoring-(node-exporter|cadvisor)"} == 0)',
    legend='down',
    unit='short',
    gridPos={ x: 6, y: 0, w: 6, h: 4 },
  ),
  panel(
    title='Disk free %',
    expr='100 * node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"}',
    legend='{{instance}} {{mountpoint}}',
    unit='percent',
    gridPos={ x: 0, y: 4, w: 12, h: 8 },
  ),
  panel(
    title='Memory available %',
    expr='100 * node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes',
    legend='{{instance}}',
    unit='percent',
    gridPos={ x: 12, y: 4, w: 12, h: 8 },
  ),
  panel(
    title='Load (5m)',
    expr='node_load5',
    legend='{{instance}}',
    unit='short',
    gridPos={ x: 0, y: 12, w: 12, h: 8 },
  ),
  panel(
    title='Load (15m)',
    expr='node_load15',
    legend='{{instance}}',
    unit='short',
    gridPos={ x: 12, y: 12, w: 12, h: 8 },
  ),
  panel(
    title='Network RX (per host)',
    expr='sum by (instance) (rate(node_network_receive_bytes_total{device!~"lo|tailscale.*"}[5m]))',
    legend='{{instance}}',
    unit='Bps',
    gridPos={ x: 0, y: 20, w: 12, h: 8 },
  ),
  panel(
    title='Network TX (per host)',
    expr='sum by (instance) (rate(node_network_transmit_bytes_total{device!~"lo|tailscale.*"}[5m]))',
    legend='{{instance}}',
    unit='Bps',
    gridPos={ x: 12, y: 20, w: 12, h: 8 },
  ),
  panel(
    title='YUSIC container memory',
    expr='container_memory_usage_bytes{name=~"yusic_.*"}',
    legend='{{instance}} / {{name}}',
    unit='bytes',
    gridPos={ x: 0, y: 28, w: 12, h: 8 },
  ),
  panel(
    title='YUSIC container CPU rate',
    expr='sum by (instance, name) (rate(container_cpu_usage_seconds_total{name=~"yusic_.*"}[5m]))',
    legend='{{instance}} / {{name}}',
    unit='percentunit',
    gridPos={ x: 12, y: 28, w: 12, h: 8 },
  ),
])
