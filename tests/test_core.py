import json
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from summary import summarise
from valorant_lab.analysis import analyse, evaluate_events
from valorant_lab.config import Config
from valorant_lab.db import Store
from valorant_lab.detectors import SceneDetector, TemplateDetector
from valorant_lab.pipeline import Pipeline, source_signature
from valorant_lab.schema import Evidence, Event, Observation
from valorant_lab.video import probe, frames, transcode


def test_original_csv_example():
    assert "52/49/24" in summarise("example.csv")


def test_invalid_csv(tmp_path):
    p=tmp_path/'invalid.csv'
    p.write_text('map,agent,result,kills,deaths,assists,lesson\nx,y,W,-1,0,0,z\n')
    with pytest.raises(ValueError): summarise(p)


def test_configuration_validation(tmp_path):
    with pytest.raises(ValueError): Config(sample_fps=float('nan'))
    with pytest.raises(ValueError): Config(recordings_dir=tmp_path,data_dir=tmp_path/'data')
    with pytest.raises(ValueError): Config(sample_fps=100)


def test_schema_rejects_bad_evidence():
    with pytest.raises(ValidationError): Observation(time=0,head_x=2,head_y=0,target_id='a')
    with pytest.raises(ValidationError): Event(time=float('nan'),kind='kill')
    o=Observation(time=0,head_x=.5,head_y=.5,target_id='a')
    with pytest.raises(ValidationError): Evidence(observations=[o,o])
    with pytest.raises(ValueError): Evidence(events=[Event(time=3,kind='kill')]).within(2)


def test_geometry_and_shot_delay():
    obs=[Observation(time=t,head_x=.4,head_y=.4,crosshair_source='manual',target_id='a') for t in (1,1.1,1.2)]
    report=analyse([Event(time=1.2,kind='shot',target_id='a')],obs,{'width':1600,'height':900,'duration':10},10)
    assert report['metrics']['alignment_error']['value']==pytest.approx(math.hypot(.1*1600/900,.1)*100)
    assert report['metrics']['vertical_offset']['value']==pytest.approx(10)
    assert report['metrics']['first_shot_delay']['value']==pytest.approx(200)
    assert report['metrics']['alignment_error']['basis']=='measured from supplied annotations'


def test_no_fabricated_metrics():
    report=analyse([],[],{'width':1600,'height':900,'duration':10},5)
    assert all(m['value'] is None for m in report['metrics'].values())
    assert not report['coaching']


def test_estimates_labelled():
    report=analyse([],[Observation(time=1,head_x=.5,head_y=.5,target_id='x')],{'width':16,'height':9,'duration':10},5)
    assert report['metrics']['alignment_error']['basis']=='estimate'
    assert report['metrics']['first_shot_delay']['value'] is None


def test_coaching_requires_repeated_engagements():
    obs=[Observation(time=base+offset,head_x=.5,head_y=.3,target_id=str(base),crosshair_source='manual') for base in (1,3,5) for offset in (0,.1,.2)]
    report=analyse([],obs,{'width':16,'height':9,'duration':10},10)
    assert report['coaching'][0]['frequency']==3
    assert report['coaching'][0]['evidence']==[1,3,5]
    sparse=analyse([],obs[:3],{'width':16,'height':9,'duration':10},10)
    assert not sparse['coaching']


def test_rounds_only_use_reviewed_events():
    events=[Event(time=1,kind='round_start'),Event(time=2,kind='kill'),Event(time=3,kind='kill',source='hud_template_unvalidated'),Event(time=4,kind='round_start'),Event(time=5,kind='death')]
    r=analyse(events,[],{'width':16,'height':9,'duration':10},5)
    assert [x['kills'] for x in r['rounds']]==[1,0]
    assert [x['deaths'] for x in r['rounds']]==[0,1]


def test_event_evaluation_one_to_one():
    truth=[Event(time=1,kind='kill'),Event(time=3,kind='death')]
    predicted=[Event(time=1.1,kind='kill'),Event(time=1.2,kind='kill')]
    result=evaluate_events(predicted,truth)
    assert result['kill']['precision']==.5
    assert result['kill']['recall']==1
    assert result['death']['fn']==1
    assert result['kill']['timing_mae_seconds']==pytest.approx(.1)


def test_template_and_scene_detection(tmp_path):
    template=np.random.default_rng(42).integers(0,255,(10,10),dtype=np.uint8)
    p=tmp_path/'kill.png';cv2.imwrite(str(p),template)
    frame=np.zeros((100,100,3),dtype=np.uint8)
    frame[20:30,20:30]=cv2.cvtColor(template,cv2.COLOR_GRAY2BGR)
    detector=TemplateDetector([{'path':str(p),'roi':[0,0,1,1],'kind':'kill','threshold':.99}])
    assert detector.detect(frame,1)[0][0].kind=='kill'
    assert not detector.detect(frame,2)[0]
    scene=SceneDetector(.3)
    assert not scene.detect(np.zeros_like(frame),0)[0]
    assert scene.detect(np.full_like(frame,255),1)[0][0].kind=='scene_change'


def test_database_recovery_and_invalidation(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    sid=store.enqueue('/video','a','v1')
    assert store.claim()['id']==sid
    store.checkpoint(sid,0,1,2,{'events':[]})
    store.recover()
    assert store.claim()['checkpoint']==1
    store.update(sid,state='complete',evidence='{}')
    assert store.enqueue('/video','a','v1')==sid
    assert store.get(sid)['state']=='complete'
    store.enqueue('/video','a','v2')
    assert store.get(sid)['evidence']=='{}'
    assert not store.chunks(sid)
    store.enqueue('/video','b','v2')
    assert store.get(sid)['evidence'] is None


def test_evidence_atomic_guard(tmp_path):
    store=Store(tmp_path/'db.sqlite');sid=store.enqueue('/v','a','v')
    assert not store.save_evidence(sid,{})
    store.claim();assert not store.save_evidence(sid,{})
    store.update(sid,state='complete');assert store.save_evidence(sid,{'events':[]})
    assert store.get(sid)['state']=='queued'


def test_video_sampling_and_clip(recording,tmp_path):
    assert probe(recording)['duration']==pytest.approx(3)
    samples=list(frames(recording,0,3,5,160,threading.Event()))
    assert 14<=len(samples)<=16
    assert samples[0][1].shape==(90,160,3)
    target=tmp_path/'clip.mp4'
    transcode(recording,target,threading.Event(),start=1,duration=1)
    assert probe(target)['duration']==pytest.approx(1,abs=.15)


def test_corrupt_video(tmp_path):
    p=tmp_path/'bad.mp4';p.write_bytes(b'not a video')
    with pytest.raises(ValueError):probe(p)


def test_settling_and_cache(config,recording,monkeypatch):
    pipeline=Pipeline(config)
    pipeline.scan();assert not pipeline.store.list()
    pipeline.scan();assert len(pipeline.store.list())==1
    assert pipeline.process_next()
    s=pipeline.store.list()[0];assert s['state']=='complete',s['error']
    assert pipeline.artifact_dir(s).joinpath('playback.mp4').exists()
    r=json.loads(s['report']);assert len(r['events'])==1
    assert r['events'][0]['kind']=='scene_change'
    assert r['events'][0]['time']==pytest.approx(1,abs=.15)
    assert all(m['value'] is None for m in r['metrics'].values())
    pipeline.scan();assert not pipeline.process_next()
    assert pipeline.store.save_evidence(s['id'],Evidence(events=[Event(time=1,kind='kill')]).model_dump())
    monkeypatch.setattr('valorant_lab.pipeline.frames',lambda *a: (_ for _ in ()).throw(AssertionError('cached frames decoded')))
    pipeline.process_next();assert pipeline.store.get(s['id'])['state']=='complete'


def test_resume_from_checkpoint(config,recording,monkeypatch):
    pipeline=Pipeline(config);pipeline.scan();pipeline.scan()
    from valorant_lab import pipeline as module
    original=module.frames
    def interrupted(*args):
        if args[2]>1: raise RuntimeError('simulated crash')
        yield from original(*args)
    monkeypatch.setattr(module,'frames',interrupted)
    pipeline.process_next();s=pipeline.store.list()[0]
    assert s['state']=='failed' and s['checkpoint']==1
    monkeypatch.setattr(module,'frames',original)
    assert pipeline.store.retry(s['id'])
    pipeline.process_next();s=pipeline.store.get(s['id'])
    assert s['state']=='complete',s['error']
    assert len(pipeline.store.chunks(s['id']))==3


def test_sidecar_validation(config,recording):
    Path(str(recording)+'.evidence.json').write_text(json.dumps({'schema_version':1,'events':[{'time':99,'kind':'kill'}]}))
    pipeline=Pipeline(config);pipeline.scan();pipeline.scan();pipeline.process_next()
    assert pipeline.store.list()[0]['state']=='failed'
    assert 'timestamps' in pipeline.store.list()[0]['error']


def test_missing_folder(tmp_path):
    p=Pipeline(Config(recordings_dir=tmp_path/'missing',data_dir=tmp_path/'data'))
    assert p.scan()==0
    assert 'not found' in p.scan_error


def test_numerical_validation():
    from valorant_lab.validate import evaluate
    truth=Evidence(observations=[Observation(time=1,head_x=.5,head_y=.5,target_id='a')])
    pred=Evidence(observations=[Observation(time=1.1,head_x=.5,head_y=.4,target_id='a')])
    result=evaluate(pred,truth,1600,900)
    assert result['aim']['alignment_mae_percent_frame_height']==pytest.approx(10)
    assert result['aim']['head_location_mae_pixels']==pytest.approx(90)
    assert evaluate(Evidence(),truth,1600,900)['aim']['alignment_mae_percent_frame_height'] is None


def test_telemetry_calibration_and_order(tmp_path):
    from valorant_lab.telemetry import load_telemetry, mouse_distance
    p=tmp_path/'mouse.jsonl'
    p.write_text('{"time":1,"dx":3,"dy":4}\n{"time":2,"dx":0,"dy":0,"fired":true}\n')
    samples=list(load_telemetry(p,video_offset=.5))
    assert samples[0].time==1.5
    assert mouse_distance(samples)['angular_path_degrees'] is None
    assert mouse_distance(samples,.2)['angular_path_degrees']==1
    p.write_text('{"time":2,"dx":0,"dy":0}\n{"time":1,"dx":0,"dy":0}\n')
    with pytest.raises(ValueError):list(load_telemetry(p))


def test_tactical_progress_does_not_invent_baselines():
    from valorant_lab.analysis import coaching_progress
    report={'coaching':[{'title':'X','basis':'reviewer interpretation','frequency':3,'denominator':6}]}
    assert coaching_progress(report,None)['coaching'][0]['progress']['delta_percentage_points'] is None
    previous={'coaching':[{'title':'X','basis':'reviewer interpretation','frequency':3,'denominator':3}]}
    assert coaching_progress(report,previous)['coaching'][0]['progress']['delta_percentage_points']==-50


def test_round_end_stops_round_counts():
    events=[Event(time=0,kind='round_start'),Event(time=1,kind='round_end'),Event(time=2,kind='kill')]
    report=analyse(events,[],{'width':16,'height':9,'duration':10},5)
    assert report['rounds'][0]['kills']==0
    assert report['rounds'][0]['end']==1


def test_single_worker_lock(config):
    from filelock import Timeout
    first,second=Pipeline(config),Pipeline(config)
    first.start()
    try:
        with pytest.raises(Timeout): second.start()
    finally:
        first.close()


def test_onnx_adapter_contract_and_tracks():
    from valorant_lab.detectors import ONNXHeadDetector
    detector=ONNXHeadDetector.__new__(ONNXHeadDetector)
    class Session:
        def run(self,*args):return [np.array([[.4,.3,.5,.4,.9,0]],dtype=np.float32)]
    detector.session=Session();detector.input='images';detector.tracks={};detector.counter=0
    _, first=detector.detect(np.zeros((90,160,3),dtype=np.uint8),1)
    _, second=detector.detect(np.zeros((90,160,3),dtype=np.uint8),1.1)
    assert first[0].target_id==second[0].target_id
    assert first[0].source=='onnx'
    assert first[0].crosshair_source=='assumed_center'


def test_csv_rejects_extra_cells(tmp_path):
    p=tmp_path/'bad.csv'
    p.write_text('map,agent,result,kills,deaths,assists,lesson\nx,y,W,1,2,3,hello,extra\n')
    with pytest.raises(ValueError,match='header'):summarise(p)
