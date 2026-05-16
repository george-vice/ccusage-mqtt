from ccusage_mqtt.usage_rate import RingBuffer


def test_ring_buffer_starts_empty():
    rb = RingBuffer(capacity=6)
    assert len(rb) == 0
    assert rb.timespan_sec() == 0.0


def test_ring_buffer_fifo_wrap():
    rb = RingBuffer(capacity=3)
    for ts, pct in [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0), (3.0, 13.0)]:
        rb.add(ts, pct)
    assert len(rb) == 3
    assert rb.oldest() == (1.0, 11.0)
    assert rb.latest() == (3.0, 13.0)
    assert rb.timespan_sec() == 2.0


def test_ring_buffer_clear():
    rb = RingBuffer(capacity=6)
    rb.add(0.0, 10.0)
    rb.clear()
    assert len(rb) == 0
