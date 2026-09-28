from types import MappingProxyType

# Read-only payloads: TicketService.create and update write into the dict they
# receive, so callers pass a copy, e.g. dict(service_add_ticket_payload).

service_add_ticket_payload = MappingProxyType({
    "category": "Default",
    "title": "Test",
    "resolution": "8,7",
    "priority": "High",
    "date_of_incident": "2024-11-20",
    "channel": "Channel A",
    "flags": "Default",
})


service_add_ticket_payload_bad_resolution = MappingProxyType({
    "category": "Default",
    "title": "Test",
    "resolution": "sdasdasadsda",
    "priority": "High",
    "date_of_incident": "2024-11-20",
    "channel": "Channel A",
    "flags": "Default",
})


service_add_ticket_payload_bad_resolution_day = MappingProxyType({
    "category": "Default",
    "title": "Test",
    "resolution": "99,5",
    "priority": "High",
    "date_of_incident": "2024-11-20",
    "channel": "Channel A",
    "flags": "Default",
})


service_add_ticket_payload_bad_resolution_hour = MappingProxyType({
    "category": "Default",
    "title": "Test",
    "resolution": "1,54",
    "priority": "High",
    "date_of_incident": "2024-11-20",
    "channel": "Channel A",
    "flags": "Default",
})


service_update_ticket_payload = MappingProxyType({
    "category": "Default",
    "title": "TestUpdate",
    "status": "OPEN",
    "resolution": "1,4",
    "priority": "Medium",
    "dateOfIncident": "2024-11-20",
    "channel": "Channel A",
    "flags": "Default",
})
