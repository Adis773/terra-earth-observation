"""Terra report: compact branded PDF generated from the analysis result."""
import io, os, base64
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from xml.sax.saxutils import escape
from pathlib import Path

FONT_DIR=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
REG=FONT_DIR/'arial.ttf'; BOLD=FONT_DIR/'arialbd.ttf'
if not REG.exists():
    REG=Path(__file__).parent/'fonts'/'DejaVuSans.ttf'
    BOLD=Path(__file__).parent/'fonts'/'DejaVuSans-Bold.ttf'
pdfmetrics.registerFont(TTFont('Terra',str(REG)));pdfmetrics.registerFont(TTFont('TerraBold',str(BOLD)))
BLACK=HexColor('#171916');GRAY=HexColor('#6d716b');LIGHT=HexColor('#eceee9')
P=ParagraphStyle('body',fontName='Terra',fontSize=9,leading=14,textColor=BLACK)
SM=ParagraphStyle('small',parent=P,fontSize=7.5,leading=11,textColor=GRAY)
HD=ParagraphStyle('heading',fontName='TerraBold',fontSize=12,leading=16,textColor=BLACK)
TITLE=ParagraphStyle('title',fontName='TerraBold',fontSize=27,leading=32,textColor=BLACK)
CELL=ParagraphStyle('cell',parent=P,fontSize=8,leading=11)
RIGHT=ParagraphStyle('right',parent=CELL,alignment=TA_RIGHT)
def txt(value): return escape(str(value))
def number(v):
    return '—' if v is None else f'{v:,.2f}'.replace(',', ' ')

def decorate(c,doc):
    width,height=A4;c.setStrokeColor(HexColor('#dadcd7'));c.line(23*mm,height-23*mm,width-23*mm,height-23*mm)
    c.setFillColor(BLACK);c.setStrokeColor(BLACK);c.setLineWidth(1.3);c.circle(23*mm+6,height-19*mm+6,5,stroke=1,fill=0);c.circle(23*mm+6,height-19*mm+6,2.4,stroke=0,fill=1);c.setFont('TerraBold',17);c.drawString(23*mm+16,height-19*mm,'terra.')
    c.setFont('Terra',7);c.setFillColor(GRAY);c.drawRightString(width-23*mm,height-18*mm,'ANALYTICAL REPORT' if getattr(doc,'report_lang','en')=='en' else 'АНАЛИТИЧЕСКИЙ ОТЧЁТ')
    c.line(23*mm,19*mm,width-23*mm,19*mm);c.setFont('TerraBold',9);c.setFillColor(BLACK);c.setLineWidth(.8);c.circle(23*mm+3,13*mm+3,3,stroke=1,fill=0);c.circle(23*mm+3,13*mm+3,1.4,stroke=0,fill=1);c.drawString(23*mm+10,13*mm,'terra.')
    c.setFont('Terra',7);c.setFillColor(GRAY);c.drawRightString(width-23*mm,13*mm,f'{doc.page}')

def seal():
    # Brand artwork, not a signature or an official certification.
    from reportlab.graphics.shapes import Drawing, Circle, String, Line
    d=Drawing(75,75);d.add(Circle(37.5,37.5,34,fillColor=None,strokeColor=BLACK,strokeWidth=.7));d.add(Circle(37.5,37.5,29,fillColor=None,strokeColor=BLACK,strokeWidth=.4));d.add(String(37.5,43,'TERRA',fontName='TerraBold',fontSize=9,textAnchor='middle',fillColor=BLACK));d.add(Line(19,37,56,37,strokeWidth=.5,strokeColor=BLACK));d.add(String(37.5,27,'ANALYTICS',fontName='Terra',fontSize=6,textAnchor='middle',fillColor=BLACK));return d

def table(rows,widths):
    t=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#f2f3f0')),('LINEBELOW',(0,0),(-1,0),.6,HexColor('#cfd1cb')),('LINEBELOW',(0,1),(-1,-1),.3,LIGHT),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6)]));return t

def build_report(result,exports,language='en'):
    words={'ДАННЫЕ НАБЛЮДЕНИЯ ЗЕМЛИ':'EARTH OBSERVATION DATA','Изменение леса':'Forest change','Покрытие территории':'Land cover','ПЛОЩАДЬ АНАЛИЗА':'ANALYSED AREA','ДОСТУПНЫЕ ДАННЫЕ':'VALID COVERAGE','ИСТОЧНИК':'SOURCE','Координаты области':'Area coordinates','Результаты анализа':'Analysis results','Показатель':'Metric','Площадь, га':'Area, ha','Лес до':'Forest before','Лес после':'Forest after','Предполагаемая потеря':'Estimated tree loss','Стабильный лес':'Stable forest','Снимки и результат':'Images and result','До':'Before','После':'After','Потеря деревьев':'Tree loss','Категория':'Category','Изм., га':'Change, ha','Доля, %':'Share, %','Примечание':'Notes','Подготовлено Terra':'Prepared by Terra','Не обнаружено':'Not detected','Деревья':'Trees','Вода':'Water','Затопленная растительность':'Flooded vegetation','Посевы':'Crops','Застройка':'Built area','Открытая почва':'Bare ground','Снег и лёд':'Snow and ice','Травянистая растительность':'Rangeland','Появление деревьев':'Tree gain',' га':' ha'}
    def tr(x): return words.get(x,x) if language=='en' else x
    buffer=io.BytesIO();doc=SimpleDocTemplate(buffer,pagesize=A4,leftMargin=23*mm,rightMargin=23*mm,topMargin=30*mm,bottomMargin=25*mm,title='Terra - Analysis Report',author='Terra');doc.report_lang=language;W=164*mm
    forest='scenes' in result
    period=' — '.join([x['date'] for x in result['scenes']] if forest else map(str,result['years']))
    heading='Изменение леса' if forest else 'Покрытие территории'
    story=[Paragraph(tr('ДАННЫЕ НАБЛЮДЕНИЯ ЗЕМЛИ'),SM),Spacer(1,5*mm),Paragraph(tr(heading),TITLE),Spacer(1,3*mm),Paragraph(txt(period),P),Spacer(1,4*mm)]
    observations=result.get('scenes_metadata',[]) if not forest else []
    if any(len(x.get('observations',[]))>1 for x in observations):
        periods=[' / '.join(y['date'] for y in x.get('observations',[])) for x in observations]
        story.extend([Paragraph(('Composite acquisition dates: ' if language=='en' else 'Даты снимков в мозаике: ')+txt(' — '.join(periods)),SM),Spacer(1,3*mm)])
    b=result['bbox']; coordinates=', '.join(f'{x:.5f}' for x in b)
    source='Landsat / Sentinel-2; ML-Project' if forest else result['source']['name']
    metadata=[[Paragraph(tr('ПЛОЩАДЬ АНАЛИЗА'),SM),Paragraph(tr('ДОСТУПНЫЕ ДАННЫЕ'),SM),Paragraph(tr('ИСТОЧНИК'),SM)],[Paragraph(number(result['area_ha'])+tr(' га'),HD),Paragraph(number(result['common_coverage_percent'] if forest else result['coverage_percent'])+' %',HD),Paragraph(txt(source),CELL)]]
    story.extend([table(metadata,[W*.3,W*.3,W*.4]),Spacer(1,4*mm),Paragraph(tr('Координаты области'),SM),Spacer(1,1*mm),Paragraph(txt(coordinates)+'  (WGS84)',P),Spacer(1,4*mm)])
    if forest:
        data=[['Показатель','Площадь, га'],['Лес до',number(result['forest_before_ha'])],['Лес после',number(result['forest_after_ha'])],['Предполагаемая потеря',number(result['forest_loss_ha'])],['Стабильный лес',number(result['forest_stable_ha'])]]
        story.extend([Paragraph(tr('Результаты анализа'),HD),Spacer(1,3*mm),table([[Paragraph(txt(tr(x)),CELL if j==0 else RIGHT) for j,x in enumerate(row)] for row in data],[W*.68,W*.32]),Spacer(1,3*mm)])
        imgs=[]
        for name,label in [('before','До'),('after','После'),('loss','Потеря деревьев')]:
            key=result['images'][name].split('/')[-1].removesuffix('.png');raw=exports.get(key)
            if raw: imgs.append([Image(io.BytesIO(raw),width=50*mm,height=50*mm,kind='proportional'),Paragraph(tr(label),SM)])
        if len(imgs)==3:
            story.extend([Paragraph(tr('Снимки и результат'),HD),Spacer(1,3*mm),Table([[x[0] for x in imgs],[x[1] for x in imgs]],colWidths=[W/3]*3,hAlign='LEFT',style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0)])),Spacer(1,3*mm)])
        n=result['ndvi'];story.extend([Paragraph(('NDVI in loss area: ' if language=='en' else 'NDVI в зоне потери: ')+number(n['before_loss'])+' → '+number(n['after_loss']),P),Spacer(1,3*mm)])
    else:
        compare=len(result['years'])==2
        data=[['Категория',str(result['years'][0])+(', ha' if language=='en' else ', га'),str(result['years'][-1])+(', ha' if language=='en' else ', га'),'Изм., га'] if compare else ['Категория','Площадь, га','Доля, %']]
        for r in result['rows']:
            if 'area_ha' in r:data.append([r['label'],'—','—',number(r['area_ha'])])
            elif compare:data.append([r['label'],('Не обнаружено' if r['class']=='trees' and r['before_ha']==0 else number(r['before_ha'])),('Не обнаружено' if r['class']=='trees' and r['after_ha']==0 else number(r['after_ha'])),number(r['change_ha'])])
            else:data.append([r['label'],('Не обнаружено' if r['class']=='trees' and r['before_ha']==0 else number(r['before_ha'])),number(r['before_percent'])])
        widths=[W*.4,W*.2,W*.2,W*.2] if compare else [W*.5,W*.25,W*.25]
        story.extend([Paragraph(tr('Результаты анализа'),HD),Spacer(1,3*mm),table([[Paragraph(txt(tr(x)),CELL if j==0 else RIGHT) for j,x in enumerate(row)] for row in data],widths),Spacer(1,7*mm)])
    story.extend([Paragraph(tr('Примечание'),HD),Spacer(1,2*mm),Paragraph(txt(('Model estimates may be inaccurate. Clouds, seasonality, mixed pixels and regional differences can affect the result. Tree loss is not proof of deforestation.' if language=='en' else result['warning'])),SM),Spacer(1,4*mm),Table([[Paragraph('<font name="TerraBold" size="11">terra.</font><br/>'+tr('Подготовлено Terra')+'<br/>'+datetime.now().strftime('%d.%m.%Y'),SM),seal()]],colWidths=[W-80,80],style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(1,0),(1,0),'RIGHT'),('LEFTPADDING',(0,0),(-1,-1),0)]))])
    if not forest and result.get('map_overlays'):
        from PIL import Image as PILImage
        colors={'water':'#3187b8','trees':'#2f8952','flooded_vegetation':'#8969ae','crops':'#e0b03d','built_up':'#c55048','bare_ground':'#af8358','snow_ice':'#b8cbd7','rangeland':'#97b458','forest_loss':'#e63f36','forest_gain':'#2db472'}
        selected={row.get('class') for row in result['rows']}
        panels=[]
        for phase,title in [('before','До'),('after','После'),('change','Изменения')]:
            layers=result['map_overlays'].get(phase,{})
            if not isinstance(layers,dict): raise ValueError('Invalid map layers')
            composite=None
            for category,uri in layers.items():
                if category not in selected or not uri: continue
                if category not in colors or not isinstance(uri,str) or not uri.startswith('data:image/png;base64,') or len(uri)>1800000: raise ValueError('Invalid map image')
                raw=base64.b64decode(uri.split(',',1)[1],validate=True)
                image=PILImage.open(io.BytesIO(raw))
                if image.format!='PNG' or image.width*image.height>1048576: raise ValueError('Invalid map size')
                image=image.convert('RGBA')
                if composite is None: composite=PILImage.new('RGBA',image.size,(242,244,240,255))
                if image.size!=composite.size: raise ValueError('Map dimensions differ')
                composite=PILImage.alpha_composite(composite,image)
            if composite is not None:
                output=io.BytesIO();composite.convert('RGB').save(output,format='PNG');output.seek(0)
                label=({'До':'Before','После':'After','Изменения':'Change'}[title] if language=='en' else title)
                if phase in ('before','after'): label+=' · '+str(result['years'][0 if phase=='before' else -1])
                panels.append((label,output))
        if panels:
            story.extend([PageBreak(),Paragraph('LAND-COVER MAPS' if language=='en' else 'КАРТЫ ПОКРЫТИЯ',SM),Spacer(1,4*mm),Paragraph('Classification and change' if language=='en' else 'Классификация и изменения',TITLE),Spacer(1,4*mm),Paragraph('Modelled classes within the selected area. Grey areas have no displayed class. These maps are not satellite photographs.' if language=='en' else 'Классы модели в выбранной области. Серым показаны участки без отображаемого класса. Это карты классификации, а не спутниковые снимки.',SM),Spacer(1,5*mm)])
            for label,output in panels:
                story.extend([Paragraph(txt(label),HD),Spacer(1,2*mm),Image(output,width=88*mm,height=48*mm,kind='proportional',hAlign='LEFT'),Spacer(1,4*mm)])
            legend=[]
            for row in result['rows']:
                key=row.get('class');color=colors.get(key)
                if color: legend.append('<font color="'+color+'">&#9632;</font> '+txt(tr(row.get('label',key))))
            if legend: story.extend([Paragraph('  &nbsp;&nbsp;  '.join(legend),SM),Spacer(1,4*mm)])
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate);return buffer.getvalue()
