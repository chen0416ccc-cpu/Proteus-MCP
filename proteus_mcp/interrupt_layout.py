"""Deterministic parallel-channel layout for the interrupt teaching example."""

from .native9 import GRID


def plan(placements, rotations):
    from .geometry import transform_xy
    from .devices import definition
    from .native9 import PIN_ORDER
    from .library import default_library
    coordinates={}
    from .native9 import pin_xy
    for ref,pos in placements.items():
        device=('AT89C51' if ref=='U1' else '7SEG-MPX4-CC' if ref=='DSP1' else 'LED-RED' if ref.startswith('D') else
                'BUTTON' if ref.startswith('SW') else 'BC547' if ref=='Q1' else 'BUZZER' if ref=='BZ1' else
                'CRYSTAL' if ref=='X1' else 'CAP-ELEC' if ref=='CR' else 'CAP' if ref in ('CX1','CX2','CB') else 'RES')
        from .geometry import orientation
        item={'device':device,'origin':[v*GRID for v in pos],'transform':orientation(rotations.get(ref,0),0)}
        if device not in PIN_ORDER:
            spec=definition(device,str(default_library()/'ACTIVE.LIB') if device=='BUZZER' else None)
            item['_embedded_definition']=spec
            numbers=[p['number'] for p in spec['pins']]
        else:numbers=PIN_ORDER[device]
        for number in numbers:
            point=pin_xy(item,number)
            if point is not None:coordinates[ref+'.'+number]=[v/GRID for v in point]
    result={'junctions':{},'terminals':{},'wires':[],'labels':{},'component_labels':{}}
    def node(name,xy):
        key='@'+name;result['junctions'][key]=list(xy);coordinates[key]=list(xy);return key
    def terminal(name,net,xy):
        key='@'+name;result['terminals'][key]={'net':net,'at':list(xy)};coordinates[key]=list(xy);return key
    def wire(net,a,b,*middle):
        points=[coordinates[a],*map(list,middle),coordinates[b]]
        clean=[]
        for p in points:
            if not clean or clean[-1]!=p:clean.append(p)
        result['wires'].append({'net':net,'ends':[a,b],'points':clean})
    def power(pin,net,end):wire(net,pin,terminal(pin.replace('.','_'),net,end))

    # P1 channels maintain row order: each lower LED uses the next outer lane.
    for i in range(8):
        row=12-4*i
        wire(f'LED{i+1}_A',f'RL{i+1}.1',f'D{i+1}.1')
        x=-23-i
        wire(f'LED{i+1}_K',f'D{i+1}.2',f'U1.{i+1}',[x,row],[x,-13-i])
        power(f'RL{i+1}.2','VCC',[-51,row])
        result['labels'][f'LED{i+1}_K']=[-35,row-1]
    # Eight signal channels fan monotonically into the display.
    for i,label in enumerate('ABCDEFGH'):
        x=12+3*i;y=5-i
        j=node('seg_'+label,[x,8])
        wire('SEG_'+label,'U1.'+str(39-i),j,[x,y])
        wire('SEG_'+label,j,'RS'+str(i)+'.1')
        wire('SEG_'+label,j,'PU'+str(i)+'.1')
        target=coordinates['DSP1.'+label]
        level=23+i*.5
        wire(label+'_OUT','RS'+str(i)+'.2','DSP1.'+label,[x,level],[target[0],level])
        power('PU'+str(i)+'.2','VCC',[x+1,15])
        result['labels']['SEG_'+label]={'visible':False}
        result['labels'][label+'_OUT']={'visible':False}
        # Run text along the narrow vertical corridors, not across next-column copper.
        result['component_labels']['RS'+str(i)]={
            'ref':{'at':[x+1,20.5],'height':1.2,'width':.65,'rotation':90},
            'value':{'at':[x+1,17.5],'height':1,'width':.6,'rotation':90}}
        result['component_labels']['PU'+str(i)]={
            'ref':{'at':[x+1.5,10.5],'height':1.2,'width':.65,'rotation':90},
            'value':{'at':[x+1.5,7.5],'height':1,'width':.6,'rotation':90}}
        result['terminals']['@PU'+str(i)+'_2']['label']={
            'at':[x+1.5,14],'height':1,'width':.6,'rotation':90}
    # P2 digit selection goes around, not through, the segment array.
    for i in range(4):
        start=coordinates['U1.'+str(21+i)];target=coordinates['DSP1.'+str(i+1)]
        outside=46+i;bottom=start[1];top=22+i
        wire('DIG_'+str(i+1),'U1.'+str(21+i),'DSP1.'+str(i+1),
             [outside,bottom],[outside,top],[target[0],top])
        result['labels']['DIG_'+str(i+1)]={'visible':False}
    # Crystal and reset components stay near the MCU, outside LED lanes.
    a=node('xtal1',[-43,28]);b=node('xtal2',[-34,24]);r=node('reset',[-10,30])
    wire('XTAL1','X1.1',a)
    wire('XTAL1','CX1.1',a,[-46,28])
    wire('XTAL1','U1.19',a,[-20,5],[-20,30],[-43,30])
    wire('XTAL2','X1.2',b,[-34,28])
    wire('XTAL2','CX2.1',b)
    wire('XTAL2','U1.18',b,[-21,2],[-21,24])
    wire('RESET','CR.2',r,[-10,32])
    wire('RESET','RR.1',r)
    wire('RESET','U1.9',r,[-19,-2],[-19,30])
    power('CX1.2','GND',[-46,17]);power('CX2.2','GND',[-30,17])
    power('CR.1','VCC',[-17,32]);power('RR.2','GND',[-4,23])
    power('CB.1','VCC',[4,35]);power('CB.2','GND',[4,25])
    power('U1.31','VCC',[-22,-9])
    # Input controls and buzzer occupy the lower strip with two ordered lanes.
    for i in range(2):
        x=12+13*i;j=node('int'+str(i),[x,-26-i*4])
        start=coordinates['U1.'+str(12+i)]
        wire('INT'+str(i),'U1.'+str(12+i),j,[7-i,start[1]],[7-i,-26-i*4])
        wire('INT'+str(i),'RP'+str(i+1)+'.1',j)
        switch=coordinates['SW'+str(i+1)+'.1']
        wire('INT'+str(i),j,'SW'+str(i+1)+'.1',[x,switch[1]])
        power('RP'+str(i+1)+'.2','VCC',[x,-19-i*4])
        power('SW'+str(i+1)+'.2','GND',[switch[0]+7,switch[1]])
        ref='SW'+str(i+1);sx,sy=PLACEMENTS[ref]
        result['component_labels'][ref]={'ref':{'at':[sx-2,sy+3],'height':1.2,'width':.65},
                                         'value':{'visible':False}}
        result['labels']['INT'+str(i)]={'at':[sx-1.5,sy-2.5],'height':1,'width':.6}
    wire('BUZZER_DRIVE','U1.17','RB.1',[5,-20],[5,-33.5],[40,-33.5],[40,-32])
    wire('BUZZER_BASE','RB.2','Q1.2')
    wire('BUZZER_LOW','Q1.1','BZ1.2',[52,-25],[52,-22],[55,-22])
    power('Q1.3','GND',[52,-39]);power('BZ1.1','VCC',[55,-18])
    # Diagnostic pullups are short ordered vertical branches below the MCU.
    for i,(name,pin) in enumerate((('ISR0_ACTIVE','25'),('ISR1_ACTIVE','26'),('COUNT_EVENT','15'),('WRAP_EVENT','14'))):
        start=coordinates['U1.'+pin];end=coordinates['RD'+str(i)+'.1'];x=.5+i*.5
        wire(name,'U1.'+pin,'RD'+str(i)+'.1',[x,start[1]],[x,-27-i*2],[end[0],-27-i*2])
        power('RD'+str(i)+'.2','VCC',[end[0],-41])
        result['labels'][name]={'visible':False}
        x=-12+6*i
        result['component_labels']['RD'+str(i)]={
            'ref':{'at':[x+1.5,-35],'height':1.2,'width':.65},
            'value':{'at':[x+1.5,-36.5],'height':1,'width':.6}}
        result['terminals']['@RD'+str(i)+'_2']['label']={'at':[x-1,-42],'height':1,'width':.6}
    # Intermediate nodes retain their simulator names without redundant ink.
    for name in ('XTAL1','XTAL2','BUZZER_BASE','BUZZER_LOW',*(f'LED{i+1}_A' for i in range(8))):
        result['labels'][name]={'visible':False}
    result['labels']['RESET']={'at':[-17,28],'height':1,'width':.6}
    result['labels']['BUZZER_DRIVE']={'at':[14,-36],'height':1,'width':.6}
    result['component_labels']['CR']={'ref':{'at':[-14,38],'height':1.2,'width':.65},
                                     'value':{'at':[-14,36.5],'height':1,'width':.6}}
    for i in range(8):
        row=12-4*i
        result['component_labels'][f'D{i+1}']={'ref':{'at':[-38,row+2.5],'height':1.2,'width':.65},
                                              'value':{'visible':False}}
        result['component_labels'][f'RL{i+1}']={'ref':{'at':[-49,row+2],'height':1.2,'width':.6},
                                               'value':{'at':[-45,row+2],'height':1,'width':.6}}
    return result


PLACEMENTS={
    'U1':[-16,6],'DSP1':[12,39],
    'X1':[-38,28],'CX1':[-46,22],'CX2':[-30,22],
    'CR':[-13,32],'RR':[-4,28],'CB':[4,30],
    'SW1':[18,-28],'SW2':[31,-32],'RP1':[12,-23],'RP2':[25,-27],
    'RB':[43,-32],'Q1':[51,-32],'BZ1':[57,-21],
}
ROTATIONS={'CX1':270,'CX2':270,'RR':270,'CB':270,'RP1':90,'RP2':90}
for i in range(8):
    PLACEMENTS.update({f'D{i+1}':[-37,12-i*4],f'RL{i+1}':[-47,12-i*4],f'RS{i}':[12+3*i,20],f'PU{i}':[13+3*i,10]})
    ROTATIONS.update({f'D{i+1}':90,f'RL{i+1}':180,f'RS{i}':90,f'PU{i}':90})
for i in range(4):
    PLACEMENTS['RD'+str(i)]=[-12+6*i,-36];ROTATIONS['RD'+str(i)]=270
