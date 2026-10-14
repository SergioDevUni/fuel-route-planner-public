"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const tripId = document.body.dataset.tripId;
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  const mapElement = byId("map");
  const stopList = byId("stop-list");
  let map = null;
  let mapBounds = null;
  let stopMarkers = [];

  function money(value) {
    const amount = Number(value);
    return Number.isFinite(amount)
      ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount)
      : "—";
  }

  function unitPrice(value) {
    const parts = String(value).trim().match(/^(\d+)(?:\.(\d*))?$/);
    if (!parts) return "—";
    // Slice the API decimal string to truncate without floating-point rounding.
    const whole = parts[1].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const fraction = (parts[2] || "").slice(0, 3).padEnd(3, "0");
    return `$${whole}.${fraction}`;
  }

  function quantity(value, decimals = 2) {
    const amount = Number(value);
    return Number.isFinite(amount) ? amount.toFixed(decimals) : "—";
  }

  function text(element, value) {
    element.textContent = value == null ? "" : String(value);
    return element;
  }

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) text(element, content);
    return element;
  }

  function showGuidance(message, heading = "Open your trip") {
    byId("workspace").hidden = true;
    text(byId("error-heading"), heading);
    text(byId("error-message"), message);
    byId("page-error").hidden = false;
  }

  function locationLabel(location) {
    return location.name ? `${location.name}, ${location.state}` : location.state;
  }

  function addFact(label, value) {
    const row = node("div");
    row.append(node("dt", "", label), node("dd", "", value));
    byId("route-facts").append(row);
  }

  function selectStop(index) {
    const cards = [...stopList.querySelectorAll(".stop-card")];
    cards.forEach((card, position) => card.setAttribute("aria-current", position === index ? "true" : "false"));
    if (map && stopMarkers[index]) {
      const marker = stopMarkers[index];
      map.panTo(marker.getLatLng(), { animate: false });
      marker.openPopup();
    }
  }

  function renderStops(stops) {
    stopList.replaceChildren();
    if (!stops.length) {
      stopList.append(node("p", "empty-stops", "No fuel purchase is needed for this trip."));
      return;
    }
    stops.forEach((stop, index) => {
      const card = node("button", "stop-card");
      card.type = "button";
      card.setAttribute("aria-label", `Show fuel stop ${index + 1}: ${stop.name}`);
      card.setAttribute("aria-current", "false");
      const head = node("div", "stop-head");
      head.append(node("span", "stop-number", index + 1));
      const titleGroup = node("span");
      titleGroup.append(
        node("span", "stop-title", stop.name),
        node("span", "stop-location", `${stop.city}, ${stop.state} · CSV station ${stop.station_id}`)
      );
      head.append(titleGroup);
      card.append(head);
      const rows = node("div", "stop-rows");
      [
        ["Price per gallon", unitPrice(stop.price_per_gallon)],
        ["Buy", `${quantity(stop.purchase_gallons)} gal`],
        ["Purchase cost", money(stop.purchase_cost)],
      ].forEach(([label, value]) => {
        const row = node("div", "stop-row");
        row.append(node("span", "", label), node("strong", "", value));
        rows.append(row);
      });
      card.append(rows);
      card.addEventListener("click", () => selectStop(index));
      stopList.append(card);
    });
  }

  function renderSummary(trip) {
    const stops = Array.isArray(trip.fuel_stops) ? trip.fuel_stops : [];
    const start = locationLabel(trip.start);
    const finish = locationLabel(trip.finish);
    text(byId("trip-heading"), `${start} to ${finish}`);
    text(byId("route-subtitle"), `${quantity(trip.route.distance_miles)} miles · ${stops.length} fuel ${stops.length === 1 ? "stop" : "stops"}`);
    text(byId("total-cost"), money(trip.costs.fuel_purchases_cost));
    text(byId("consumed-cost"), money(trip.costs.total_fuel_cost));
    text(byId("stops-heading"), `Fuel stops · ${stops.length}`);
    renderStops(stops);
    const facts = byId("route-facts");
    facts.replaceChildren();
    addFact("Total distance", `${quantity(trip.route.distance_miles)} mi`);
    if (trip.route.extra_distance_miles != null) addFact("Extra distance", `${quantity(trip.route.extra_distance_miles)} mi`);
    if (trip.costs.fuel_consumed_gallons != null) addFact("Fuel consumed", `${quantity(trip.costs.fuel_consumed_gallons)} gal`);
    if (trip.costs.ending_fuel_gallons != null) addFact("Fuel at finish", `${quantity(trip.costs.ending_fuel_gallons)} gal`);
    const legs = byId("leg-list");
    legs.replaceChildren();
    (trip.route.legs || []).forEach((leg, index) => {
      legs.append(node("li", "", `Leg ${index + 1}: ${quantity(leg.distance_miles)} mi · arrive with ${quantity(leg.arrival_fuel_gallons)} gal`));
    });
    if (!legs.children.length) legs.append(node("li", "", "No driving distance."));
  }

  function showMapMessage(message) {
    const element = byId("map-message");
    text(element, message);
    element.hidden = false;
  }

  function fallbackMap(trip) {
    const geometry = trip.route.geometry;
    const coordinates = geometry.type === "Point" ? [geometry.coordinates] : geometry.coordinates;
    if (!Array.isArray(coordinates) || !coordinates.length) return;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 1000 650");
    svg.setAttribute("class", "fallback-route");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Verified route shape without map tiles");
    const xs = coordinates.map((point) => Number(point[0]));
    const ys = coordinates.map((point) => Number(point[1]));
    const minX = xs.reduce((value, item) => Math.min(value, item), Infinity);
    const maxX = xs.reduce((value, item) => Math.max(value, item), -Infinity);
    const minY = ys.reduce((value, item) => Math.min(value, item), Infinity);
    const maxY = ys.reduce((value, item) => Math.max(value, item), -Infinity);
    const width = Math.max(maxX - minX, 0.00001);
    const height = Math.max(maxY - minY, 0.00001);
    const projected = coordinates.map((point) => [
      70 + 860 * (Number(point[0]) - minX) / width,
      580 - 510 * (Number(point[1]) - minY) / height,
    ]);
    const line = document.createElementNS(svg.namespaceURI, "polyline");
    line.setAttribute("points", projected.map((point) => point.join(",")).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#166e54");
    line.setAttribute("stroke-width", "7");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("stroke-linejoin", "round");
    svg.append(line);
    [projected[0], projected[projected.length - 1]].forEach((point) => {
      const circle = document.createElementNS(svg.namespaceURI, "circle");
      circle.setAttribute("cx", point[0]);
      circle.setAttribute("cy", point[1]);
      circle.setAttribute("r", "12");
      circle.setAttribute("fill", "#fff");
      circle.setAttribute("stroke", "#174e3f");
      circle.setAttribute("stroke-width", "5");
      svg.append(circle);
    });
    mapElement.replaceChildren(svg);
    byId("fit-route").hidden = true;
    showMapMessage("Basemap unavailable. The saved route shape and stop details remain available.");
  }

  function renderMap(trip) {
    const geometry = trip.route.geometry;
    if (!window.L || !geometry || !["LineString", "Point"].includes(geometry.type)) {
      fallbackMap(trip);
      return;
    }
    const points = geometry.type === "Point" ? [geometry.coordinates] : geometry.coordinates;
    if (!Array.isArray(points) || !points.length) {
      fallbackMap(trip);
      return;
    }
    const latLngs = points.map((point) => [Number(point[1]), Number(point[0])]);
    map = L.map(mapElement, { zoomControl: true, scrollWheelZoom: false, preferCanvas: true });
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 18,
    });
    let tileErrors = 0;
    tiles.on("tileerror", () => {
      tileErrors += 1;
      if (tileErrors === 3) showMapMessage("Basemap tiles are unavailable. The saved route and stops are still shown.");
    });
    tiles.addTo(map);
    if (geometry.type === "LineString") {
      const line = L.polyline(latLngs, { color: "#166e54", weight: 6, opacity: .95 }).addTo(map);
      mapBounds = line.getBounds();
    } else {
      mapBounds = L.latLngBounds(latLngs);
    }
    const endIcon = L.divIcon({ className: "endpoint-pin", iconSize: [21, 21], iconAnchor: [10, 10] });
    L.marker(latLngs[0], { icon: endIcon, title: "Start" }).addTo(map);
    if (latLngs.length > 1) L.marker(latLngs[latLngs.length - 1], { icon: endIcon, title: "Finish" }).addTo(map);
    stopMarkers = (trip.fuel_stops || []).map((stop, index) => {
      const coordinates = stop.coordinates;
      const icon = L.divIcon({ className: "stop-pin", html: String(index + 1), iconSize: [42, 42], iconAnchor: [21, 21] });
      const marker = L.marker([Number(coordinates.latitude), Number(coordinates.longitude)], {
        icon, title: `Fuel stop ${index + 1}: ${stop.name}`, keyboard: true,
      }).addTo(map);
      const popup = node("div");
      popup.append(
        node("div", "popup-title", `${index + 1}  ${stop.name}`),
        node("div", "popup-detail", `Buy ${quantity(stop.purchase_gallons)} gal at ${unitPrice(stop.price_per_gallon)}/gal`),
        node("div", "popup-detail", `Purchase cost ${money(stop.purchase_cost)}`),
      );
      marker.bindPopup(popup);
      marker.on("click", () => selectStop(index));
      return marker;
    });
    const fit = () => {
      if (mapBounds && mapBounds.isValid()) {
        if (latLngs.length === 1) map.setView(latLngs[0], 12, { animate: false });
        else map.fitBounds(mapBounds.pad(.12), { animate: false, maxZoom: 13 });
      }
    };
    byId("fit-route").addEventListener("click", fit);
    fit();
    window.addEventListener("resize", () => map.invalidateSize());
  }

  async function loadTrip() {
    if (!token || !/^[A-Za-z0-9_-]{20,128}$/.test(token)) {
      showGuidance("Open the map URL returned with your trip result to access your trip.", "Open your trip link");
      return;
    }
    try {
      const response = await fetch(`/api/v1/trips/${encodeURIComponent(tripId)}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!response.ok) {
        showGuidance(response.status === 410
          ? "This trip link has expired. Request a new trip result."
          : "Open the map URL returned with your trip result, or create a new trip.");
        return;
      }
      const trip = await response.json();
      if (!trip || !trip.route || !trip.start || !trip.finish || !trip.costs) throw new Error("Incomplete trip result");
      renderSummary(trip);
      renderMap(trip);
    } catch (_) {
      showGuidance("Check your connection and open your trip link again.");
    }
  }

  loadTrip();
})();
