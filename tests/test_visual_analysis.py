from pathlib import Path
import tempfile
import unittest
from test_pipeline import fixture
from book_illustrator.core import Book
from book_illustrator.visual_analysis import VisualAnalyzer,sentences,validate_fact,visual_selection

class VisualAnalysisTests(unittest.TestCase):
    def test_invalid_episode_links_are_quarantined_without_losing_valid_scenes(self):
        paragraphs=[{'id':'p1','text':'One'},{'id':'p2','text':'Two'}]
        valid={'start_paragraph_id':'p1','end_paragraph_id':'p2','title':'Scene',
               'summary':'Event','kind':'action','characters':[],'visible_moment':5,
               'visual_interest':4,'narrative_importance':3,'novelty':3}
        foreign={**valid,'start_paragraph_id':'another-chapter'}
        reversed_range={**valid,'start_paragraph_id':'p2','end_paragraph_id':'p1'}
        chosen,rejected=visual_selection([foreign,valid,reversed_range],paragraphs,5)
        self.assertEqual(len(chosen),1)
        self.assertEqual(chosen[0]['episode_id'],'e002')
        self.assertEqual(len(rejected),2)
        self.assertIn('outside this chapter',rejected[0]['rejected_reason'])
        self.assertIn('reversed',rejected[1]['rejected_reason'])

    def test_all_invalid_episode_links_leave_chapter_text_only(self):
        bad={'start_paragraph_id':'foreign','end_paragraph_id':'foreign','title':'Scene',
             'summary':'Event','kind':'action','characters':[],'visible_moment':5,
             'visual_interest':4,'narrative_importance':3,'novelty':3}
        chosen,rejected=visual_selection([bad],[{'id':'p1','text':'One'}],5)
        self.assertEqual(chosen,[])
        self.assertEqual(len(rejected),1)

    def test_narrative_importance_cannot_rescue_abstract_episode(self):
        paragraphs=[{'id':f'p{i}','text':'x'*30} for i in range(3)]
        def episode(i,kind,visible):
            return {'start_paragraph_id':f'p{i}','end_paragraph_id':f'p{i}','title':kind,
                'summary':'Event','kind':kind,'characters':[],'visible_moment':visible,
                'visual_interest':4,'narrative_importance':5,'novelty':4}
        chosen,rejected=visual_selection([episode(0,'abstract',1),episode(1,'environment',4),
                                          episode(2,'interaction',4)],paragraphs,3)
        self.assertEqual(len(chosen),2)
        self.assertEqual(rejected[0]['kind'],'abstract')

    def test_sentences_are_literal_source_and_evidence_is_person_scoped(self):
        paragraph={'id':'p1','text':'У него тёмные глаза. Волосы густые. Чисто ли он выбрит? Да!'}
        items=sentences(paragraph)
        self.assertTrue(all(s['text'] in paragraph['text'] for s in items))
        fact={'description_en':'Dark eyes','category':'eyes','state':'permanent','source_sentence_ids':['p1.s01']}
        validate_fact(fact,{s['id']:s for s in items})
        with self.assertRaisesRegex(ValueError,'supplied'):validate_fact(fact,{})
        fact['description_en']='Dark eyes and thick hair'
        with self.assertRaisesRegex(ValueError,'Separate'):validate_fact(fact,{s['id']:s for s in items})

    def test_separate_appearance_call_copies_sentence_not_entire_paragraph(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);fixture(root/'book.fb2');book=Book(root/'book.fb2')
            chapter=book.chapters[1];paras=chapter['paragraphs'];pid=paras[0]['id'];calls=[]
            class Backend:
                last_usage=None
                def complete(self,system,payload,schema):
                    calls.append(payload)
                    if len(calls)==1:
                        return {'episodes':[{'start_paragraph_id':p['id'],'end_paragraph_id':p['id'],
                            'title':'Event','summary':'A visible event','kind':'action','characters':['Иван'],
                            'visible_moment':4,'visual_interest':4,'narrative_importance':3,'novelty':4}
                            for p in paras], 'characters':[{'name':'Иван','aliases':[],'appearance_paragraph_ids':[pid]}]}
                    if len(calls)==2:
                        return {'characters':[{'name':'Иван','facts':[{'description_en':'Grey hair',
                            'category':'hair','state':'permanent','source_sentence_ids':[pid+'.s02']}]}]}
                    return {'scenes':[{'episode_id':e['episode_id'],'description':'One event',
                                      'image_prompt':'One event in a room','characters':['Иван']}
                                     for e in payload['episodes']]}
            run=root/'run';run.mkdir()
            analysis,summary=VisualAnalyzer(Backend(),run,{}).analyze(book,chapter,2)
            fact=analysis['characters'][0]['appearance'][0]
            self.assertEqual(fact['quote'],'У него седые волосы.')
            self.assertEqual(len(calls),3)
            self.assertEqual(summary['fact_count'],1)
            self.assertEqual(len(calls[2]['episodes']),2)
