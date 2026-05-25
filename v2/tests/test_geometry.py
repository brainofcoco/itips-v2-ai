from itips.utils.geometry import bbox_iou, point_in_polygon


class TestBboxIou:
    def test_identical_boxes(self):
        assert bbox_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0

    def test_disjoint_boxes(self):
        assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0

    def test_half_overlap(self):
        # left box: 0..10 x 0..10; right box: 5..15 x 0..10 — 50% horizontal overlap.
        iou = bbox_iou((0, 0, 10, 10), (5, 0, 15, 10))
        assert abs(iou - (50.0 / 150.0)) < 1e-6

    def test_zero_area_box(self):
        assert bbox_iou((0, 0, 0, 0), (0, 0, 10, 10)) == 0.0


class TestPointInPolygon:
    SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def test_inside(self):
        assert point_in_polygon((5.0, 5.0), self.SQUARE)

    def test_outside(self):
        assert not point_in_polygon((20.0, 5.0), self.SQUARE)

    def test_degenerate_polygon(self):
        assert not point_in_polygon((5.0, 5.0), [(0.0, 0.0), (1.0, 1.0)])
