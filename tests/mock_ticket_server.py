"""
A.E.G.I.S. Deterministic Mock Ticket Booking Server
===================================================
Phase 4: Local test fixture providing a deterministic ticket booking portal
with real DOM semantics, accessible controls, and verified booking IDs.
Zero external internet or payment dependencies.
"""

import http.server
import socketserver
import threading
import json
import urllib.parse
from typing import Optional

MOCK_PORT = 8765

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CineAEGIS Ticket Booking Portal</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
    .container { max-width: 900px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
    h1, h2, h3 { color: #38bdf8; margin-top: 0; }
    .search-bar { display: flex; gap: 10px; margin-bottom: 24px; }
    input[type="text"], input[type="search"], select { flex: 1; padding: 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 16px; }
    button { padding: 12px 20px; border-radius: 6px; border: none; background: #0284c7; color: #fff; font-weight: bold; cursor: pointer; transition: 0.2s; font-size: 15px; }
    button:hover { background: #0369a1; }
    .movie-card { display: flex; justify-content: space-between; align-items: center; background: #334155; padding: 16px; border-radius: 8px; margin-bottom: 12px; }
    .stage { display: none; margin-top: 20px; border-top: 1px solid #475569; padding-top: 20px; }
    .stage.active { display: block; }
    .pills { display: flex; gap: 10px; margin: 15px 0; }
    .pill { background: #1e293b; border: 1px solid #0284c7; padding: 8px 16px; border-radius: 20px; cursor: pointer; color: #38bdf8; }
    .pill.selected { background: #0284c7; color: #fff; }
    .seat-grid { display: grid; grid-template-columns: repeat(6, 45px); gap: 10px; margin: 20px 0; justify-content: center; }
    .seat { width: 45px; height: 45px; background: #10b981; border: none; border-radius: 6px; color: #fff; font-weight: bold; cursor: pointer; }
    .seat.selected { background: #f59e0b; }
    .seat.occupied { background: #64748b; cursor: not-allowed; }
    .summary-box { background: #0f172a; border: 1px solid #38bdf8; padding: 16px; border-radius: 8px; margin: 20px 0; }
    .summary-row { display: flex; justify-content: space-between; margin-bottom: 8px; }
    .badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: bold; }
    .badge-pending { background: #f59e0b; color: #000; }
    .badge-confirmed { background: #10b981; color: #fff; }
  </style>
</head>
<body>
  <div class="container">
    <h1>CineAEGIS Ticket Booking Portal</h1>

    <!-- 1. Search Bar & City Selector -->
    <div class="search-bar">
      <input type="search" id="search-input" role="searchbox" placeholder="Search for Movies, Events..." aria-label="Search for Movies" />
      <select id="city-select" aria-label="Select City">
        <option value="Kochi">Kochi</option>
        <option value="Kollam">Kollam</option>
        <option value="Mumbai">Mumbai</option>
      </select>
      <button id="search-btn" role="button" onclick="performSearch()">Search</button>
    </div>

    <!-- 2. Search Results / Catalog -->
    <div id="catalog-section">
      <h3>Movies in <span id="current-city-label">Kochi</span></h3>
      <div id="movie-list">
        <div class="movie-card" data-title="Avengers: Secret Wars">
          <div>
            <h4 class="movie-title" style="margin:0 0 6px 0;">Avengers: Secret Wars</h4>
            <span style="color:#94a3b8; font-size:14px;">Action, Sci-Fi | English | 2h 45m</span>
          </div>
          <button class="book-tickets-btn" role="button" onclick="selectMovie('Avengers: Secret Wars')">Book Tickets</button>
        </div>
        <div class="movie-card" data-title="Inception">
          <div>
            <h4 class="movie-title" style="margin:0 0 6px 0;">Inception</h4>
            <span style="color:#94a3b8; font-size:14px;">Sci-Fi, Thriller | English | 2h 28m</span>
          </div>
          <button class="book-tickets-btn" role="button" onclick="selectMovie('Inception')">Book Tickets</button>
        </div>
      </div>
    </div>

    <!-- 3. Showtimes Selection Stage -->
    <div id="stage-showtimes" class="stage">
      <h2>Select Showtimes: <span id="showtime-movie-title" style="color:#38bdf8;"></span></h2>
      <p>Venue: <strong id="venue-label">JB Cinemas Screen 1</strong> (<span id="venue-city">Kochi</span>)</p>

      <div class="pills">
        <button class="pill selected" id="date-tomorrow" onclick="selectDate('tomorrow')">Tomorrow</button>
        <button class="pill" id="date-today" onclick="selectDate('today')">Today</button>
      </div>

      <div class="pills">
        <button class="pill selected showtime-btn" data-time="09:15 PM" role="button" onclick="selectShowtime('09:15 PM')">09:15 PM</button>
        <button class="pill showtime-btn" data-time="06:30 PM" role="button" onclick="selectShowtime('06:30 PM')">06:30 PM</button>
      </div>
      <button id="proceed-seats-btn" role="button" onclick="openSeats()">Select Seats</button>
    </div>

    <!-- 4. Seat Selection Stage -->
    <div id="stage-seats" class="stage">
      <h2>Select Seats</h2>
      <p>Screen This Way ───────────────</p>
      <div class="seat-grid">
        <button class="seat" data-seat="A1" role="button" aria-label="Seat A1" onclick="toggleSeat(this, 'A1')">A1</button>
        <button class="seat" data-seat="A2" role="button" aria-label="Seat A2" onclick="toggleSeat(this, 'A2')">A2</button>
        <button class="seat" data-seat="A3" role="button" aria-label="Seat A3" onclick="toggleSeat(this, 'A3')">A3</button>
        <button class="seat" data-seat="A4" role="button" aria-label="Seat A4" onclick="toggleSeat(this, 'A4')">A4</button>
        <button class="seat occupied" data-seat="B1" disabled>B1</button>
        <button class="seat occupied" data-seat="B2" disabled>B2</button>
      </div>
      <p>Selected Seats: <strong id="selected-seats-text">None</strong></p>
      <p>Total Amount: <strong id="seat-total-price">₹0.00</strong></p>
      <button id="proceed-checkout-btn" role="button" onclick="openCheckout()">Review & Checkout</button>
    </div>

    <!-- 5. Checkout & Confirmation Gate Stage -->
    <div id="stage-checkout" class="stage">
      <h2>Booking Summary & Safety Gate</h2>
      <div class="summary-box">
        <div class="summary-row"><span>Status:</span> <span class="badge badge-pending" id="order-status-badge">PENDING_CONFIRMATION</span></div>
        <div class="summary-row"><span>Movie:</span> <strong id="summary-movie"></strong></div>
        <div class="summary-row"><span>City:</span> <span id="summary-city"></span></div>
        <div class="summary-row"><span>Venue:</span> <span id="summary-venue">JB Cinemas Screen 1</span></div>
        <div class="summary-row"><span>Date:</span> <span id="summary-date"></span></div>
        <div class="summary-row"><span>Showtime:</span> <span id="summary-time"></span></div>
        <div class="summary-row"><span>Seats:</span> <span id="summary-seats"></span></div>
        <div class="summary-row" style="font-size:18px; color:#38bdf8; border-top:1px solid #334155; padding-top:8px;">
          <span>Total Payable:</span> <strong id="summary-price"></strong>
        </div>
      </div>
      <p style="color:#f59e0b; font-size:14px;">⚠️ SAFETY GATE: Click confirm only when authorized by operator.</p>
      <button id="authorize-booking-btn" role="button" style="background:#10b981;" onclick="submitFinalBooking()">Authorize & Confirm Booking</button>
    </div>

    <!-- 6. Confirmed Stage -->
    <div id="stage-confirmed" class="stage">
      <h2 style="color:#10b981;">🎉 Booking Confirmed!</h2>
      <div class="summary-box" style="border-color:#10b981;">
        <div class="summary-row"><span>Booking State:</span> <span class="badge badge-confirmed" id="confirmed-state-badge">CONFIRMED</span></div>
        <div class="summary-row"><span>Booking Reference:</span> <strong id="booking-reference-id" style="color:#38bdf8;">BKG-AVENGERS-9941</strong></div>
        <div class="summary-row"><span>Ticket Count:</span> <span id="confirmed-seat-count"></span></div>
        <div class="summary-row"><span>Venue Gate:</span> <span>Door 2 (Validated)</span></div>
      </div>
    </div>
  </div>

  <script>
    let state = {
      movie: '',
      city: 'Kochi',
      date: 'tomorrow',
      showtime: '09:15 PM',
      seats: [],
      pricePerSeat: 250,
      totalPrice: 0,
      status: 'INITIAL'
    };

    function performSearch() {
      const q = document.getElementById('search-input').value.toLowerCase().trim();
      const city = document.getElementById('city-select').value;
      state.city = city;
      document.getElementById('current-city-label').innerText = city;

      const cards = document.querySelectorAll('.movie-card');
      cards.forEach(c => {
        const title = c.getAttribute('data-title').toLowerCase();
        if (!q || title.includes(q)) {
          c.style.display = 'flex';
        } else {
          c.style.display = 'none';
        }
      });
    }

    function selectMovie(title) {
      state.movie = title;
      state.city = document.getElementById('city-select').value;
      document.getElementById('showtime-movie-title').innerText = title;
      document.getElementById('venue-city').innerText = state.city;
      
      document.getElementById('catalog-section').style.display = 'none';
      document.getElementById('stage-showtimes').classList.add('active');
    }

    function selectDate(d) {
      state.date = d;
      document.querySelectorAll('#stage-showtimes .pill').forEach(b => {
        if (b.id.startsWith('date-')) b.classList.remove('selected');
      });
      document.getElementById('date-' + d).classList.add('selected');
    }

    function selectShowtime(t) {
      state.showtime = t;
      document.querySelectorAll('.showtime-btn').forEach(b => b.classList.remove('selected'));
      const activeBtn = Array.from(document.querySelectorAll('.showtime-btn')).find(b => b.getAttribute('data-time') === t);
      if (activeBtn) activeBtn.classList.add('selected');
    }

    function openSeats() {
      document.getElementById('stage-showtimes').classList.remove('active');
      document.getElementById('stage-seats').classList.add('active');
    }

    function toggleSeat(btn, seatNum) {
      if (btn.classList.contains('occupied')) return;
      if (btn.classList.contains('selected')) {
        btn.classList.remove('selected');
        state.seats = state.seats.filter(s => s !== seatNum);
      } else {
        btn.classList.add('selected');
        state.seats.push(seatNum);
      }
      state.totalPrice = state.seats.length * state.pricePerSeat;
      document.getElementById('selected-seats-text').innerText = state.seats.join(', ') || 'None';
      document.getElementById('seat-total-price').innerText = '₹' + state.totalPrice + '.00';
    }

    function openCheckout() {
      if (state.seats.length === 0) {
        alert('Please select at least one seat.');
        return;
      }
      state.status = 'PENDING_CONFIRMATION';
      document.getElementById('summary-movie').innerText = state.movie;
      document.getElementById('summary-city').innerText = state.city;
      document.getElementById('summary-date').innerText = state.date;
      document.getElementById('summary-time').innerText = state.showtime;
      document.getElementById('summary-seats').innerText = state.seats.join(', ') + ' (' + state.seats.length + ' tickets)';
      document.getElementById('summary-price').innerText = '₹' + state.totalPrice + '.00';

      document.getElementById('stage-seats').classList.remove('active');
      document.getElementById('stage-checkout').classList.add('active');
    }

    function submitFinalBooking() {
      state.status = 'CONFIRMED';
      document.getElementById('confirmed-seat-count').innerText = state.seats.length + ' tickets (' + state.seats.join(', ') + ')';
      document.getElementById('stage-checkout').classList.remove('active');
      document.getElementById('stage-confirmed').classList.add('active');
    }
  </script>
</body>
</html>
"""


class MockTicketHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML_CONTENT.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging in test output
        pass


class MockTicketServer:
    """Threaded HTTP server serving the deterministic mock ticketing portal."""

    def __init__(self, port: int = MOCK_PORT):
        self.port = port
        self.server: Optional[socketserver.TCPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        socketserver.TCPServer.allow_reuse_address = True
        self.server = socketserver.TCPServer(("127.0.0.1", self.port), MockTicketHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[MOCK_SERVER] Mock ticket portal running at http://127.0.0.1:{self.port}/")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.thread = None
        print("[MOCK_SERVER] Mock ticket portal stopped.")


# Global singleton instance for testing
mock_ticket_server = MockTicketServer()

if __name__ == "__main__":
    import time
    s = MockTicketServer()
    s.start()
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.stop()
