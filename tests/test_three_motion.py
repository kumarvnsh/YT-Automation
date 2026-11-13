import copy,math,unittest
from src.three_motion import validate_plan, inspection_errors

def plan():
 return {'objects':[{'id':'bridge','kind':'arch_bridge','position':[0,0,0],'size':[8,4,1],'color':'#bba07a','spans':4,'tiers':2}],
         'shots':[{'start':i*2.5,'from':[7,5,12],'to':[5,4,10],'look':[0,2,0],'cue':'FOLLOW THE WATER'} for i in range(4)],
         'animations':[]}

class ThreeMotionTests(unittest.TestCase):
 def test_accepts_complete_moving_camera_plan(self):
  result=validate_plan(plan(),10)
  self.assertEqual(len(result['shots']),4)
 def test_rejects_static_camera_and_long_idle_shot(self):
  p=plan();p['shots'][0]['to']=p['shots'][0]['from'][:]
  with self.assertRaisesRegex(ValueError,'camera'):validate_plan(p,10)
  p=plan();p['shots']=p['shots'][:2]
  with self.assertRaisesRegex(ValueError,'shot'):validate_plan(p,10)
 def test_rejects_bad_geometry_and_unbounded_parameters(self):
  for key,value in [('kind','script'),('size',[1,-2,1]),('position',[0,float('nan'),0]),('spans',10000)]:
   with self.subTest(key=key):
    p=plan();p['objects'][0][key]=value
    with self.assertRaises(ValueError):validate_plan(p,10)
 def test_rejects_duplicate_ids_unknown_animation_targets(self):
  p=plan();p['objects'].append(copy.deepcopy(p['objects'][0]))
  with self.assertRaisesRegex(ValueError,'id'):validate_plan(p,10)
  p=plan();p['animations']=[{'object':'missing','property':'rotation','axis':'y','start':0,'end':2,'from':0,'to':1}]
  with self.assertRaisesRegex(ValueError,'target'):validate_plan(p,10)
 def test_rejects_nonjson_behavior_and_invalid_flow(self):
  p=plan();p['objects'].append({'id':'path','kind':'flow','points':[[0,0,0]],'speed':1})
  with self.assertRaises(ValueError):validate_plan(p,10)
  p=plan();p['shots'][0]['start']=.4
  with self.assertRaises(ValueError):validate_plan(p,10)
 def test_sampled_blank_or_stationary_visuals_fail_publication(self):
  self.assertTrue(inspection_errors([0,0,0],[0,0],[0,.5,1]))
  self.assertTrue(inspection_errors([.02,.02,.02],[0,0],[0,.5,1]))
  self.assertFalse(inspection_errors([.12,.15,.12],[.08,.1],[0,.5,1]))

if __name__=='__main__':unittest.main()
