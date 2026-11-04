#!/bin/bash
sqlite3 /opt/flight-monitor/data/flight_monitor.db "ALTER TABLE monitors ADD COLUMN excluded_flights TEXT DEFAULT '[]';"
echo "column added"
sqlite3 /opt/flight-monitor/data/flight_monitor.db ".schema monitors" | grep excluded
