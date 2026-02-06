(function () {
  const runId = window.__RUN_ID__;
  if (!runId) return;

  const tableResults = window.__RESULTS__ || [];
  const pieCanvas = document.getElementById("pie");
  if (pieCanvas && window.Chart && tableResults.length) {
    const labels = tableResults.map(r => r.polygon);
    const values = tableResults.map(r => r.weight_percent);
    const colors = labels.map((_, i) => `hsl(${(i * 47) % 360} 60% 55%)`);
    new Chart(pieCanvas, {
      type: "pie",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: colors }]
      },
      options: {
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                const v = ctx.parsed || 0;
                return `${ctx.label}: ${v.toFixed(2)}%`;
              }
            }
          }
        }
      }
    });
  }

  fetch(`/results/${runId}/data`)
    .then((resp) => resp.json())
    .then((data) => {
      const map = L.map("map", { zoomControl: true }).setView([20, 0], 2);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map);

      const polyLayer = L.geoJSON(data.polygons, {
        style: {
          color: "#1a3d5c",
          weight: 2,
          fillColor: "#4d8cc4",
          fillOpacity: 0.2,
        },
        onEachFeature: function (feature, layer) {
          if (feature.properties && feature.properties.name) {
            layer.bindPopup(feature.properties.name);
          }
        },
      }).addTo(map);

      const weightMin = data.weight_min ?? 0;
      const weightMax = data.weight_max ?? 0;
      const range = Math.max(1e-9, weightMax - weightMin);

      const heatPoints = data.points.map((p) => {
        const lat = p[0];
        const lon = p[1];
        const w = p[2] ?? 0;
        const normalized = (w - weightMin) / range; // 0..1
        const intensity = 0.05 + 0.95 * Math.max(0, Math.min(1, normalized));
        return [lat, lon, intensity];
      });

      if (L.heatLayer) {
        L.heatLayer(heatPoints, {
          radius: 18,
          blur: 14,
          maxZoom: 12,
          minOpacity: 0.25,
          gradient: {
            0.0: "#1f9d55",
            0.2: "#7bc96f",
            0.4: "#d9f0a3",
            0.6: "#fec44f",
            0.8: "#fe9929",
            1.0: "#b10026",
          },
        }).addTo(map);
      } else {
        // Fallback: show points as colored circle markers if heat plugin fails
        const toColor = (t) => {
          const r = Math.round(31 + (177 - 31) * t);
          const g = Math.round(157 + (0 - 157) * t);
          const b = Math.round(85 + (38 - 85) * t);
          return `rgb(${r},${g},${b})`;
        };

        data.points.forEach((p) => {
          const lat = p[0];
          const lon = p[1];
          const w = p[2] ?? 0;
          const t = Math.max(0, Math.min(1, (w - weightMin) / range));
          L.circleMarker([lat, lon], {
            radius: 3,
            color: toColor(t),
            weight: 1,
            fillColor: toColor(t),
            fillOpacity: 0.8,
          }).addTo(map);
        });
      }

      const group = L.featureGroup([polyLayer]);
      try {
        map.fitBounds(group.getBounds().pad(0.1));
      } catch (e) {
        map.setView([20, 0], 2);
      }
    })
    .catch((err) => {
      console.error(err);
    });
})();
