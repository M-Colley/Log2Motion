import base64
import io
import math
import re
import os
from datetime import datetime
from xml.sax.saxutils import escape
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable

# --------------
# COLOR PALETTE
# --------------
COLOR_BLACK = colors.HexColor("#000000")
COLOR_GREY = colors.HexColor("#666666")
COLOR_ORANGE = colors.HexColor("#ffad40")
LINE_COLOR = colors.HexColor("#e3e4e8")
BACKGROUND_COLOR = colors.HexColor("#faf9f7")

# Colors for the bullet points.
DOT_COLORS = [
    colors.HexColor("#fd9f91"),
    colors.HexColor("#e46264"),
    colors.HexColor("#ff8000"),
    colors.HexColor("#ffad40")
]

# ------------
# FONT SETUP
# ------------
ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = Path(os.environ.get("LOG2MOTION_FONT_DIR", Path(__file__).resolve().parent / "fonts"))
ANTON_PATH = FONT_DIR / "Anton-Regular.ttf"
INTER_PATH = FONT_DIR / "Inter.ttf"
INTER_EXTRABOLD_PATH = FONT_DIR / "Inter-ExtraBold.ttf"

for font_name, font_path, fallback in [
    ('Anton', ANTON_PATH, 'Helvetica-Bold'),
    ('Inter-Regular', INTER_PATH, 'Helvetica'),
    ('Inter-ExtraBold', INTER_EXTRABOLD_PATH, 'Helvetica-Bold'),
]:
    if font_path.is_file():
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    else:
        pdfmetrics.registerFont(pdfmetrics.Font(font_name, fallback, 'WinAnsiEncoding'))
    pdfmetrics.registerFontFamily(font_name, normal=font_name, bold=font_name,
                                  italic=font_name, boldItalic=font_name)


def run_date_time(run):
    """Use the recorded timestamp; never substitute an invented report date."""
    timestamp = run.get('timestamp', '')
    try:
        recorded = datetime.fromisoformat(timestamp)
        return recorded.strftime('%d.%m.%Y'), recorded.strftime('%H:%M:%S')
    except (TypeError, ValueError):
        return str(run.get('date', 'Unknown date')), str(run.get('time', 'Unknown time'))


def outcome_description(metrics):
    summary = metrics.get('outcome_summary')
    if not summary or summary.get('status') == 'unknown':
        return 'Gesture outcomes unavailable; trajectories do not confirm successful clicks.'
    text = f"{summary['successful']} of {summary['total']} gestures succeeded."
    if summary.get('failed'):
        text += f" {summary['failed']} failed ({summary.get('timed_out', 0)} timed out)."
    if summary.get('unknown'):
        text += f" {summary['unknown']} outcomes unknown."
    return text


def outcome_success_rate(metrics):
    summary = metrics.get('outcome_summary') or {}
    if summary.get('total') and not summary.get('unknown'):
        return summary['successful'] / summary['total']
    return None


class SingleRunDynamicLines(Flowable):
    """
    Draw the static line-grid used on the 'Simulation Run Result' page:
      1) a full-width horizontal line at the current flow position,
      2) a vertical divider between the metrics (left) and diagram (right)
         columns, running down to the footer line,
      3) a horizontal line in the middle of the diagram column (visually
         separates the two diagrams stacked below one another).
    """
    def __init__(
        self,
        line_width,
        x_split,
        y_footer,
        x_end,
        thickness=0.75,
        color=LINE_COLOR
    ):
        super().__init__()

        self.line_width = line_width
        self.x_split = x_split
        self.y_footer = y_footer
        self.x_end = x_end
        self.thickness = thickness
        self.color = color

    def wrap(self, availWidth, availHeight):
        return self.line_width, self.thickness

    def drawOn(self, canvas, x, y, _sW=0):
        canvas.saveState()

        canvas.setStrokeColor(self.color)
        canvas.setLineWidth(self.thickness)

        # Draw full-width horizontal line at the current flow position.
        y_line2 = y

        canvas.line(
            x,
            y_line2,
            x + self.line_width,
            y_line2
        )

        # Draw vertical divider line.
        canvas.line(
            self.x_split,
            y_line2,
            self.x_split,
            self.y_footer
        )

        # Draw horizontal line splitting the two stacked diagrams.
        y_mid_diagram = (
            (y_line2 + self.y_footer) / 2.0
            + 1 * mm
        )

        canvas.line(
            self.x_split,
            y_mid_diagram,
            self.x_end,
            y_mid_diagram
        )

        canvas.restoreState()

    def draw(self):
        pass


class CompareRunDynamicLines(Flowable):
    """
    Draw only the vertical divider line used on the COMPARE RUN pdf.
    """
    def __init__(self, line_width, x_split, y_footer, thickness=0.75, color=LINE_COLOR):
        super().__init__()
        self.line_width = line_width
        self.x_split = x_split
        self.y_footer = y_footer
        self.thickness = thickness
        self.color = color

    def wrap(self, availWidth, availHeight):
        return self.line_width, 0

    def drawOn(self, canvas, x, y, _sW=0):
        canvas.saveState()
        canvas.setStrokeColor(self.color)
        canvas.setLineWidth(self.thickness)

        # Draw the vertical line from the current position (y) to the footer.
        canvas.line(self.x_split, y, self.x_split, self.y_footer)

        canvas.restoreState()


class VCenterFlowable(Flowable):
    """
    Center the element vertically within all remaining space on the current page.
    """
    def __init__(self, inner):
        super().__init__()
        self.inner = inner
        self._avail_h = 0
        self._content_h = 0
        self._content_w = 0

    def wrap(self, availWidth, availHeight):
        w, h = self.inner.wrap(availWidth, availHeight)
        self._content_w = availWidth
        self._content_h = h
        self._avail_h = availHeight
        return availWidth, availHeight

    def draw(self):
        y_bottom = max(0.0, (self._avail_h - self._content_h) / 2.0)
        self.inner.drawOn(self.canv, 0, y_bottom)


class NumberedCanvas(canvas.Canvas):
    """
    Draw the shared footer on every page.
    """
    def __init__(self, *args, footer_type="single", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.footer_type = footer_type

    def showPage(self):
        """Instead of finalizing the page immediately, remember its state so
           the correct "X / total" can be placed once its clear how many pages
           the document has in total."""
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        """Draw the footer rule + centered 'Page X / Y' label for one page."""
        self.saveState()

        page_w, _ = A4
        y_footer = 18 * mm

        line_w = page_w * 0.80
        x_start = (page_w - line_w) / 2.0
        x_end = x_start + line_w

        self.setStrokeColor(LINE_COLOR)
        self.setLineWidth(0.75)
        self.line(x_start, y_footer, x_end, y_footer)

        self.setFont("Inter-Regular", 10)
        self.setFillColor(COLOR_GREY)
        text = f"Page {self._pageNumber} / {page_count}"
        self.drawCentredString(page_w / 2.0, 10 * mm, text)

        self.restoreState()


def decode_image(img_input):
    """
    Normalize an incoming image reference (either a raw base64 string, a
    'data:image/...;base64,' data-URI, or None) into a BytesIO buffer that
    ReportLab's Image flowable can consume. Return None if the decoding fails.
    """
    if not img_input:
        return None
    if isinstance(img_input, str) and img_input.startswith("data:image"):
        img_input = img_input.split(",")[1]
    try:
        image_data = base64.b64decode(img_input)
        return io.BytesIO(image_data)
    except Exception:
        return None


# ---------------
# SINGLE RUN PDF
# ---------------
def generate_single_run_pdf(run):
    """
    Build the 'Simulation Run Result' PDF for a single run.
    """
    buffer = io.BytesIO()

    # Page dimensions: A4 (210 mm x 297 mm).
    page_w, page_h = A4
    margin_x = 18 * mm
    content_w = page_w - (2 * margin_x)

    # Y-coordinates for the header/footer lines that stay fixed on every page.
    y_header = page_h - 38 * mm
    y_footer = 18 * mm

    # 30-70 Split of the content width (metrics column / diagram column).
    col_left_w = content_w * 0.30
    col_right_w = content_w * 0.70
    x_split = margin_x + col_left_w

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin_x, rightMargin=margin_x,
        topMargin=12*mm, bottomMargin=22*mm
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('Title', parent=styles['Normal'], fontName='Anton', fontSize=24, leading=24, textColor=COLOR_BLACK)
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontName='Inter-Regular', fontSize=11, leading=12, textColor=COLOR_GREY)
    style_sec_black = ParagraphStyle('SecBlack', parent=styles['Normal'], fontName='Anton', fontSize=14, leading=15, textColor=COLOR_BLACK)
    style_lbl = ParagraphStyle('Lbl', parent=styles['Normal'], fontName='Inter-Regular', fontSize=8, leading=10, textColor=COLOR_GREY)
    style_val = ParagraphStyle('Val', parent=styles['Normal'], fontName='Inter-Regular', fontSize=14, leading=14, textColor=COLOR_BLACK)
    style_txt = ParagraphStyle('Txt', parent=styles['Normal'], fontName='Inter-Regular', fontSize=10, leading=11, textColor=COLOR_BLACK)

    def draw_page1_bg(canvas_obj, doc_obj):
        """Draw the fixed header line on page 1."""
        canvas_obj.saveState()
        canvas_obj.setStrokeColor(LINE_COLOR)
        canvas_obj.setLineWidth(0.75)

        # Header line.
        line_w = page_w * 0.80
        x_start = (page_w - line_w) / 2.0
        x_end = x_start + line_w
        canvas_obj.line(x_start, y_header, x_end, y_header)

        canvas_obj.restoreState()

    def draw_page2_bg(canvas_obj, doc_obj):
        """Draw the fixed header line and the metrics/diagram divider on page 2 onward."""
        canvas_obj.saveState()
        canvas_obj.setStrokeColor(LINE_COLOR)
        canvas_obj.setLineWidth(0.75)

        line_w = page_w * 0.80
        x_start = (page_w - line_w) / 2.0
        x_end = x_start + line_w

        # Header line.
        canvas_obj.line(x_start, y_header, x_end, y_header)

        # Vertical line between header and footer.
        canvas_obj.line(x_split, y_header, x_split, y_footer)

        canvas_obj.restoreState()

    def get_header():
        """Definition of the title and subtitle block which is repeated identically on every page."""
        run_name = run.get('name') or f"run {run.get('id') or run.get('run_id') or '1'}"
        date_str, time_str = run_date_time(run)
        run_name = escape(str(run_name))
        subtitle_text = f"Overview of the simulation {run_name} on {date_str} at {time_str}"

        return [
            Paragraph("Simulation Run Result", style_title),
            Spacer(1, 4*mm),
            Paragraph(subtitle_text, style_sub),
            Spacer(1, 4*mm)
        ]

    story = []

    # ==================== PAGE 1 ====================
    story.extend(get_header())

    # List selected elements.
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("Selected Elements", style_sec_black))
    story.append(Spacer(1, 3*mm))

    elements = run.get('selected_elements', [])
    num_elements = len(elements)

    # Generate the bullet points for the selected elements list.
    def make_dot_item(text_str, color):
        """Render a single bullet-point line with a colored dot."""
        hex_val = color.hexval()[2:]
        bullet_svg = f'<font color="#{hex_val}" size="20">&bull;</font> {escape(str(text_str))}'
        return Paragraph(bullet_svg, style_txt)

    # Define the three column layout for the selected elements list.
    if num_elements == 0:
        t_elements = Paragraph("None", style_txt)
    else:
        # Set the maximum number of lines per column.
        max_rows = max(4, math.ceil(num_elements / 3.0))

        # Dynamically determine how many columns are required.
        num_cols = min(math.ceil(num_elements / float(max_rows)), 3)

        rows = [[[] for _ in range(num_cols)] for _ in range(max_rows)]

        # Fill in the columns.
        for i, el in enumerate(elements):
            name = el if isinstance(el, str) else el.get('name', 'Unknown')
            color = DOT_COLORS[i % len(DOT_COLORS)]

            target_col = min(i // max_rows, num_cols - 1)

            rows[i % max_rows][target_col] = [make_dot_item(name, color), Spacer(1, 2 * mm)]

        # Calculate the column widths.
        elements_table_width = content_w * 0.90
        calc_col_width = elements_table_width / float(num_cols)

        t_elements = Table(rows, colWidths=[calc_col_width] * num_cols,
                           hAlign='CENTER', splitInRow=1)
        t_elements.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))

    story.append(t_elements)

    # Gap before the metrics/diagram columns begin.
    story.append(Spacer(1, 8*mm))
    story.append(
    SingleRunDynamicLines(
        line_width=page_w * 0.80,
        x_split=x_split,
        y_footer=y_footer,
        x_end=((page_w - (page_w * 0.80)) / 2.0) + (page_w * 0.80),
        thickness=0.75,
        color=LINE_COLOR
        )
    )
    story.append(Spacer(1, 10*mm))

    # Place metrics left and diagrams right.
    metrics = run.get('metrics', {}).get('current') or run.get('metrics', {})
    images = run.get('images') or run.get('chart_images') or {}

    # Left-hand column: metrics.
    left_flowables = [
        Paragraph("Metrics", style_sec_black), Spacer(1, 5*mm),
        Paragraph("GESTURE OUTCOMES", style_lbl), Spacer(1, 2*mm),
        Paragraph(outcome_description(metrics), style_txt), Spacer(1, 5*mm),
        Paragraph("MUSCLE COST", style_lbl), Spacer(1, 2*mm),
        Paragraph(f"{metrics.get('avg_muscle_cost', '—')} %", style_val), Spacer(1, 5*mm),
        Paragraph("TOTAL STEPS", style_lbl), Spacer(1, 2*mm),
        Paragraph(str(metrics.get('total_steps', '—')), style_val), Spacer(1, 5*mm),
        Paragraph("TOTAL CONTACTS", style_lbl), Spacer(1, 2*mm),
        Paragraph(str(metrics.get('total_contacts', metrics.get('contact_occurred', '—'))), style_val),
    ]

    # Right-hand column: muscle cost diagram.
    right_flowables_1 = [
        Paragraph("Diagram", style_sec_black), Spacer(1, 2*mm),
        Paragraph("MUSCLE COST", style_lbl), Spacer(1, 5*mm)
    ]
    img1_io = decode_image(images.get('muscleCostChart') or images.get('muscleCost') or images.get('muscle_cost'))
    if img1_io:
        right_flowables_1.append(Image(img1_io, width=115*mm, height=62*mm))

    t_main_1 = Table([[left_flowables, right_flowables_1]], colWidths=[col_left_w, col_right_w])
    t_main_1.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 4 * mm), # left column
        ('RIGHTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 10 * mm), # right column
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    story.append(t_main_1)
    story.append(Spacer(1, 17*mm))

    # Generate the finger trajectory diagram.
    right_flowables_2 = [
        Paragraph("Diagram", style_sec_black), Spacer(1, 2*mm),
        Paragraph("FINGER TRAJECTORY", style_lbl), Spacer(1, 5*mm)
    ]
    img2_io = decode_image(images.get('trajectoryChart') or images.get('trajectory'))
    if img2_io:
        right_flowables_2.append(Image(img2_io, width=115*mm, height=62*mm))

    t_main_2 = Table([["", right_flowables_2]], colWidths=[col_left_w, col_right_w])
    t_main_2.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 10 * mm),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    story.append(t_main_2)

    # ==================== PAGE 2 ====================
    story.append(PageBreak())
    story.extend(get_header())

    story.append(Spacer(1, 10*mm))

    right_flowables_p2 = [
        Paragraph("Diagram", style_sec_black), Spacer(1, 2*mm),
        Paragraph("CLICK TIMELINE", style_lbl), Spacer(1, 5*mm)
    ]
    img3_io = decode_image(images.get('contactEventsChart') or images.get('contactEvents') or images.get('contacts'))
    if img3_io:
        right_flowables_p2.append(Image(img3_io, width=115*mm, height=62*mm))

    t_main_p2 = Table([["", right_flowables_p2]], colWidths=[col_left_w, col_right_w])
    t_main_p2.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 10 * mm),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    story.append(t_main_p2)

    # Build the document with page numbering and canvas background lines.
    doc.build(
        story,
        canvasmaker=lambda *args, **kwargs: NumberedCanvas(*args, footer_type="single", **kwargs),
        onFirstPage=draw_page1_bg,
        onLaterPages=draw_page2_bg
    )

    buffer.seek(0)
    return buffer.getvalue()


# ------------------
# COMPARE RUNs PDF
# ------------------
def generate_compare_runs_pdf(run_a, run_b, diff):
    """
    Build the "Simulation Comparison Report" PDF that compares two
    simulation runs (run_a vs run_b) and describe the
    added / removed / reordered / modified elements between them.
    """
    buffer = io.BytesIO()

    # Page dimensions: A4.
    page_w, page_h = A4
    margin_x = 18 * mm
    content_w = page_w - (2 * margin_x)
    y_footer = 18 * mm

    # Vertical divider for every two-column layout.
    x_split_compare = margin_x + (content_w / 2.0)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin_x,
        rightMargin=margin_x,
        topMargin=18 * mm,
        bottomMargin=18 * mm
    )

    styles = getSampleStyleSheet()

    # Paragraph styles.
    style_title = ParagraphStyle('Title', parent=styles['Normal'], fontName='Anton', fontSize=24, leading=24, textColor=COLOR_BLACK)
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontName='Inter-Regular', fontSize=11, leading=14, textColor=COLOR_GREY)
    style_sec_black = ParagraphStyle('SecBlack', parent=styles['Normal'], fontName='Anton', fontSize=14, leading=15, textColor=COLOR_BLACK)
    style_sec_center = ParagraphStyle('SecCenter', parent=styles['Normal'], fontName='Anton', fontSize=14, leading=15, alignment=1, textColor=COLOR_BLACK)
    style_lbl = ParagraphStyle('Lbl', parent=styles['Normal'], fontName='Inter-Regular', fontSize=8, leading=10, textColor=COLOR_GREY)
    style_val = ParagraphStyle('Val', parent=styles['Normal'], fontName='Inter-Regular', fontSize=14, leading=14, textColor=COLOR_BLACK)
    style_txt = ParagraphStyle('Txt', parent=styles['Normal'], fontName='Inter-Regular', fontSize=10, leading=11, textColor=COLOR_BLACK)
    style_metric_label = ParagraphStyle('MetricLabel', parent=style_lbl, fontName='Anton', fontSize=9, leading=11, alignment=1, textColor=COLOR_GREY)
    style_metric_value = ParagraphStyle('MetricValue', parent=style_val, fontName='Inter-Regular', fontSize=15, leading=17, alignment=1, textColor=COLOR_BLACK)
    style_best = ParagraphStyle('BestRun', parent=style_txt, fontName='Anton', fontSize=10, leading=12, alignment=1, textColor=COLOR_ORANGE)
    style_equal = ParagraphStyle('EqualRun', parent=style_txt, fontName='Anton', fontSize=10, leading=12, alignment=1, textColor=COLOR_ORANGE)
    style_cell = ParagraphStyle('Cell', parent=style_txt, alignment=1)
    style_cell_header = ParagraphStyle('CellHeader', parent=style_txt, fontName='Inter-ExtraBold', alignment=1)

    story = []

    def make_dot_item(text_str, color):
        """Render a single bullet-point line with a colored dot."""
        hex_val = color.hexval()[2:]
        bullet_svg = f'<font color="#{hex_val}" size="20">&bull;</font> {escape(str(text_str))}'
        return Paragraph(bullet_svg, style_txt)

    def build_dynamic_elements_list(elements):
        """
        Lay out a list of element names as bulleted items, automatically
        distributed across up to 3 columns - used for the 'Selected Elements',
        'Added Elements' and 'Removed Elements' sections.
        """
        num_elements = len(elements)
        if num_elements == 0:
            return Paragraph("None", style_txt)

        # Up to 4 rows per column before a new column is started (max 3 columns).
        max_rows = max(4, math.ceil(num_elements / 3.0))
        num_cols = min(math.ceil(num_elements / float(max_rows)), 3)
        rows = [[[] for _ in range(num_cols)] for _ in range(max_rows)]

        for i, el in enumerate(elements):
            name = el if isinstance(el, str) else el.get('name', 'Unknown')
            color = DOT_COLORS[i % len(DOT_COLORS)]
            target_col = min(i // max_rows, num_cols - 1)
            rows[i % max_rows][target_col] = [make_dot_item(str(name), color), Spacer(1, 2 * mm)]

        elements_table_width = content_w * 0.90
        calc_col_width = elements_table_width / float(num_cols)

        table = Table(rows, colWidths=[calc_col_width] * num_cols,
                      hAlign='CENTER', splitInRow=1)
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        return table

    def build_separator_line():
        """
        Thin horizontal divider line spanning the full content width.
        """
        sep_line = Table([['']], colWidths=[content_w], rowHeights=[1 * mm])
        sep_line.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.5, LINE_COLOR)]))
        return sep_line

    def get_metrics(run):
        """Returns a runs current metrics dict."""
        metrics = run.get('metrics', {})
        return metrics.get('current') or metrics

    def safe_number(value):
        """Best-effort float conversion and return none for missing/invalid values."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def determine_better_run(r_a, r_b):
        """
        Score both runs on muscle cost (lower is better), total steps
        (lower is better) and total contacts (higher is better).
        Returns 'a', 'b' or 'equal'.
        """
        m_a, m_b = get_metrics(r_a), get_metrics(r_b)
        success_a, success_b = outcome_success_rate(m_a), outcome_success_rate(m_b)
        if success_a is not None and success_b is not None and success_a != success_b:
            return 'a' if success_a > success_b else 'b'
        muscle_a, muscle_b = safe_number(m_a.get('avg_muscle_cost')), safe_number(m_b.get('avg_muscle_cost'))
        steps_a, steps_b = safe_number(m_a.get('total_steps')), safe_number(m_b.get('total_steps'))
        contacts_a = safe_number(m_a.get('total_contacts', m_a.get('contact_occurred')))
        contacts_b = safe_number(m_b.get('total_contacts', m_b.get('contact_occurred')))

        score_a, score_b = 0, 0
        if muscle_a is not None and muscle_b is not None:
            if muscle_a < muscle_b: score_a += 1
            elif muscle_b < muscle_a: score_b += 1
        if steps_a is not None and steps_b is not None:
            if steps_a < steps_b: score_a += 1
            elif steps_b < steps_a: score_b += 1
        if contacts_a is not None and contacts_b is not None:
            if contacts_a > contacts_b: score_a += 1
            elif contacts_b > contacts_a: score_b += 1

        if score_a == score_b: return 'equal'
        return 'a' if score_a > score_b else 'b'

    def add_section_header(title):
        """Append a bold section title (e.g. 'Added Elements') to the story."""
        story.append(Paragraph(title, style_sec_black))
        story.append(Spacer(1, 4 * mm))

    def format_modified_value(prop, raw_value):
        """
        Format the 'Previous'/'Current' cell in the Modified Elements table
        based on its property:
          - 'Position' -> "X: <x>, Y: <y>"
          - 'Size'     -> "W: <w>, H: <h>"
        Fall back to the raw value unchanged if it can't be parsed.
        """
        if raw_value is None or raw_value == '':
            return '-'

        prop_key = (prop or '').strip().lower()

        if isinstance(raw_value, dict):
            if prop_key in ('position', 'screen position'):
                x = raw_value.get('x', raw_value.get('X', ''))
                y = raw_value.get('y', raw_value.get('Y', ''))
                return f"X: {x}, Y: {y}"
            if prop_key == 'size':
                w = raw_value.get('w', raw_value.get('W', raw_value.get('width', '')))
                h = raw_value.get('h', raw_value.get('H', raw_value.get('height', '')))
                return f"W: {w}, H: {h}"
            return str(raw_value)

        text = str(raw_value)
        if prop_key in ('position', 'screen position', 'size'):
            numbers = re.findall(r'-?\d+(?:\.\d+)?', text)
            if len(numbers) >= 2:
                if prop_key in ('position', 'screen position'):
                    return f"X: {numbers[0]}, Y: {numbers[1]}"
                return f"W: {numbers[0]}, H: {numbers[1]}"
        return text

    def build_report_header():
        """
        Build the recurring page header (title, subtitle, separator line) as
        a list of flowables instead of appending directly to `story`. This
        lets callers both (a) append it to the page and (b) measure its
        height via measure_height() before deciding whether more content
        still fits on the same page.
        """
        date_a, time_a = run_date_time(run_a)
        date_b, time_b = run_date_time(run_b)
        subtitle_text = (
            f"Overview of the comparison from simulation run 1 on {date_a} at {time_a}"
            f"<br/>and run 2 on {date_b} at {time_b}"
        )
        return [
            Paragraph("Simulation Comparison Report", style_title),
            Spacer(1, 4 * mm),
            Paragraph(subtitle_text, style_sub),
            Spacer(1, 6 * mm),
            build_separator_line(),
        ]

    def add_report_header():
        """Append the recurring page header directly to the story."""
        story.extend(build_report_header())

    def measure_height(flowables, width=None):
        """
        Measure the total height a list of flowables to decide whether a section still fits
        on the current page before falling back to a page break.
        """
        if width is None:
            width = content_w
        total = 0.0
        for f in flowables:
            try:
                _, h = f.wrap(width, 100000)
            except Exception:
                h = 0
            total += h
        return total

    def build_modified_elements_section():
        """
        Build the 'Modified Elements' heading + table as a list of
        flowables, so it can either be measured/placed right after
        'Reordered Elements' if it still fits or on its own page otherwise.
        """
        flowables = [Paragraph("Modified Elements", style_sec_black), Spacer(1, 4 * mm)]
        modified = diff.get('modified_elements', [])
        if modified:
            modified_rows = [[
                Paragraph("Element Name", style_cell_header),
                Paragraph("Property", style_cell_header),
                Paragraph("Previous", style_cell_header),
                Paragraph("Current", style_cell_header)
            ]]
            for el in modified:
                prop = el.get('property', 'N/A')
                modified_rows.append([
                    Paragraph(escape(str(el.get('name', 'N/A'))), style_cell),
                    Paragraph(str(prop), style_cell),
                    Paragraph(format_modified_value(prop, {'x': el.get('old_v1', '-'), 'y': el.get('old_v2', '-'),
                                                           'w': el.get('old_v1', '-'), 'h': el.get('old_v2', '-')}), style_cell),
                    Paragraph(format_modified_value(prop, {'x': el.get('new_v1', '-'), 'y': el.get('new_v2', '-'),
                                                           'w': el.get('new_v1', '-'), 'h': el.get('new_v2', '-')}), style_cell)
                ])
            t_mod = Table(modified_rows, colWidths=[content_w * 0.3, content_w * 0.2, content_w * 0.25, content_w * 0.25], hAlign='CENTER')
            t_mod.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, COLOR_BLACK),
                ('LINEBELOW', (0, 1), (-1, -1), 0.5, COLOR_BLACK),
                ('TOPPADDING', (0, 0), (-1, -1), 2.5 * mm),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
            ]))
            flowables.append(t_mod)
        else:
            flowables.append(Paragraph("None", style_txt))
        return flowables

    def build_diagram_column(run_label, metric_label, img_data):
        """
        Create one column of a diagram comparison pair: the heading,
        the metric label and the chart image itself.
        """
        col = [
            Paragraph(run_label, style_sec_black), Spacer(1, 2 * mm),
            Paragraph(metric_label, style_lbl), Spacer(1, 4 * mm),
        ]
        img_io = decode_image(img_data)
        if img_io:
            col.append(Image(img_io, width=content_w * 0.44, height=45 * mm, hAlign='LEFT'))
        else:
            col.append(Paragraph("No Image", style_txt))
        return col

    def build_diagram_row(metric_label, img_a_data, img_b_data):
        """
        Render one metrics Run 1 / Run 2 diagrams side by side
        """
        col_a = build_diagram_column("Diagram Run 1", metric_label, img_a_data)
        col_b = build_diagram_column("Diagram Run 2", metric_label, img_b_data)

        row_table = Table([[col_a, col_b]], colWidths=[content_w * 0.49, content_w * 0.49], hAlign='CENTER')
        row_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (0, 0), (0, 0), 4 * mm),
            ('LEFTPADDING', (1, 0), (1, 0), 4 * mm),
            ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ]))
        return row_table

    def build_diagrams_block(specs):
        """
        Stack several diagram rows into a single flowable, with a horizontal
        separator line between each consecutive pair of diagrams. Returned as
        one Table so it can be measured as a unit and handed to
        VCenterFlowable to be centered vertically as a whole block.
        """
        rows = []
        for i, (label, img_a_data, img_b_data) in enumerate(specs):
            if i > 0:
                rows.append([Spacer(1, 6 * mm)])
                rows.append([build_separator_line()])
                rows.append([Spacer(1, 6 * mm)])
            rows.append([build_diagram_row(label, img_a_data, img_b_data)])

        block = Table(rows, colWidths=[content_w])
        block.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return block

    def build_metric_card(title, run, is_winner, is_equal):
        """
        Render one 'Metrics Run X' card. The better run is highlighted
        with an orange rounded border, and its status label ('BETTER
        SIMULATION RUN' / 'EQUALLY GOOD RUN') is printed underneath.
        """
        m = get_metrics(run)
        mc_val = str(m.get('avg_muscle_cost', '—'))
        if mc_val != '—' and not mc_val.endswith('%'):
            mc_val += ' %'

        card_content = [
            Paragraph(title, style_sec_center), Spacer(1, 4 * mm),
            Paragraph(outcome_description(m), style_txt), Spacer(1, 3 * mm),
            Paragraph("MUSCLE COST", style_metric_label), Spacer(1, 1 * mm),
            Paragraph(mc_val, style_metric_value), Spacer(1, 3 * mm),
            Paragraph("TOTAL STEPS", style_metric_label), Spacer(1, 1 * mm),
            Paragraph(str(m.get('total_steps', '—')), style_metric_value), Spacer(1, 3 * mm),
            Paragraph("TOTAL CONTACTS", style_metric_label), Spacer(1, 1 * mm),
            Paragraph(str(m.get('total_contacts', m.get('contact_occurred', '—'))), style_metric_value), Spacer(1, 2 * mm)
        ]

        card_table = Table([[card_content]], colWidths=[content_w * 0.42], hAlign='CENTER')
        t_style = [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 4 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4 * mm),
            ('LEFTPADDING', (0, 0), (-1, -1), 4 * mm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4 * mm),
        ]

        # Only the winning run gets the orange highlight box.
        if is_winner:
            t_style.extend([
                ('BOX', (0, 0), (-1, -1), 2, COLOR_ORANGE),
                ('ROUNDEDCORNERS', [12, 12, 12, 12])
            ])

        card_table.setStyle(TableStyle(t_style))

        if is_equal:
            status_para = Paragraph("EQUALLY GOOD RUN", style_equal)
        elif is_winner:
            status_para = Paragraph("BETTER SIMULATION RUN", style_best)
        else:
            status_para = Paragraph("", style_txt)

        return [card_table, Spacer(1, 4 * mm), status_para]

    # ==================== PAGE 1 ====================
    add_report_header()
    story.append(Spacer(1, 6 * mm))

    better_run = determine_better_run(run_a, run_b)

    for idx, (title, run) in enumerate([("Selected Elements Run 1", run_a), ("Selected Elements Run 2", run_b)]):
        add_section_header(title)
        elements = run.get('selected_elements', [])
        story.append(build_dynamic_elements_list(elements))
        story.append(Spacer(1, 6 * mm))
        story.append(build_separator_line())
        if idx == 0:
            story.append(Spacer(1, 8 * mm))

    story.append(CompareRunDynamicLines(
        line_width=content_w,
        x_split=x_split_compare,
        y_footer=y_footer,
        thickness=0.75,
        color=LINE_COLOR
    ))

    card_a = build_metric_card("Metrics Run 1", run_a, better_run == 'a', better_run == 'equal')
    card_b = build_metric_card("Metrics Run 2", run_b, better_run == 'b', better_run == 'equal')

    col_w = content_w * 0.50
    card_w = content_w * 0.42
    side_pad = (col_w - card_w) / 2.0

    metrics_side_by_side = Table([[card_a, card_b]], colWidths=[col_w, col_w], hAlign='CENTER')
    metrics_side_by_side.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), # the shorter card (no "better run" box/label) is vertically centered against the taller one within the same row,
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), side_pad),
        ('RIGHTPADDING', (0, 0), (-1, -1), side_pad),
    ]))

    story.append(VCenterFlowable(metrics_side_by_side))

    # ==================== PAGE 2 ====================
    story.append(PageBreak())
    add_report_header()
    story.append(Spacer(1, 6 * mm))

    # Added Elements.
    add_section_header("Added Elements")
    story.append(build_dynamic_elements_list(diff.get('added_elements', [])))
    story.append(Spacer(1, 6 * mm))
    story.append(build_separator_line())
    story.append(Spacer(1, 6 * mm))

    # Removed Elements.
    add_section_header("Removed Elements")
    story.append(build_dynamic_elements_list(diff.get('removed_elements', [])))
    story.append(Spacer(1, 6 * mm))
    story.append(build_separator_line())
    story.append(Spacer(1, 6 * mm))

    # Reordered Elements.
    add_section_header("Reordered Elements")
    reordered = diff.get('reordered_elements', [])
    if reordered:
        reordered_rows = [[
            Paragraph("Element Name", style_cell_header),
            Paragraph("From Pos.", style_cell_header),
            Paragraph("To Pos.", style_cell_header)
        ]]
        for el in reordered:
            reordered_rows.append([
                Paragraph(escape(str(el.get('name', 'N/A'))), style_cell),
                Paragraph(str(el.get('from_number', 'N/A')), style_cell),
                Paragraph(str(el.get('to_number', 'N/A')), style_cell)
            ])
        t_reorder = Table(reordered_rows, colWidths=[content_w / 3.0] * 3, hAlign='CENTER')
        t_reorder.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, COLOR_BLACK),
            ('LINEBELOW', (0, 1), (-1, -1), 0.5, COLOR_BLACK),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
        ]))
        story.append(t_reorder)
    else:
        story.append(Paragraph("None", style_txt))

    # Modified Elements.
    usable_h = page_h - doc.topMargin - doc.bottomMargin
    modified_section_gap = [Spacer(1, 6 * mm), build_separator_line(), Spacer(1, 6 * mm)]
    modified_section = build_modified_elements_section()

    last_break_idx = max(i for i, fl in enumerate(story) if isinstance(fl, PageBreak))
    page2_flowables_so_far = story[last_break_idx + 1:]
    used_so_far = measure_height(page2_flowables_so_far)
    modified_needed = measure_height(modified_section_gap + modified_section)

    if used_so_far + modified_needed <= usable_h:
        story.extend(modified_section_gap)
        story.extend(modified_section)
    else:
        story.append(PageBreak())
        add_report_header()
        story.append(Spacer(1, 6 * mm))
        story.extend(modified_section)

    story.append(Spacer(1, 8 * mm))

    imgs_a = run_a.get('images', run_a.get('chart_images', {}))
    imgs_b = run_b.get('images', run_b.get('chart_images', {}))

    diagram_specs = [
        ("MUSCLE COST", imgs_a.get('muscleCost'), imgs_b.get('muscleCost')),
        ("FINGER TRAJECTORY", imgs_a.get('trajectory'), imgs_b.get('trajectory')),
        ("CLICK TIMELINE", imgs_a.get('contactEvents'), imgs_b.get('contactEvents')),
    ]
    has_diagrams = any(img_a_data or img_b_data for _, img_a_data, img_b_data in diagram_specs)

    # If no diagrams follow close off the page with its own separator line.
    if not has_diagrams:
        story.append(build_separator_line())

    # ==================== PAGE 3 ====================
    if has_diagrams:
        header_h = measure_height(build_report_header())
        available_for_diagrams = usable_h - header_h

        # Greedily pack as many diagram rows as fit on one page before starting a new one.
        remaining_specs = diagram_specs
        while remaining_specs:
            chosen = remaining_specs[:1]
            for n in range(2, len(remaining_specs) + 1):
                candidate = remaining_specs[:n]
                if measure_height([build_diagrams_block(candidate)]) <= available_for_diagrams:
                    chosen = candidate
                else:
                    break

            story.append(PageBreak())
            add_report_header()

            story.append(CompareRunDynamicLines(
                line_width=content_w,
                x_split=x_split_compare,
                y_footer=y_footer,
                thickness=0.75,
                color=LINE_COLOR
            ))

            story.append(VCenterFlowable(build_diagrams_block(chosen)))

            remaining_specs = remaining_specs[len(chosen):]

    # Build the document.
    doc.build(
        story,
        canvasmaker=lambda *args, **kwargs: NumberedCanvas(*args, footer_type="compare", **kwargs)
    )

    buffer.seek(0)
    return buffer.getvalue()
