"""No-live-post regression tests for thread composition and record routing."""
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'collectors/spotify/streams/tools/scripts'))
import post_daily_streams_thread as daily
import post_best_day_since_twitter as records
import finalize_update as finalize
import spotify_app_cards as cards


class ThreadTests(unittest.TestCase):
    def test_five_components_in_one_ordered_thread(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp, ExitStack() as stack:
            assert Path(tmp).resolve().parent == Path(__file__).resolve().parent
            out = Path(tmp)
            validate = stack.enter_context(patch.object(daily.history_store, 'validate_released_active_history_complete'))
            stack.enter_context(patch.object(daily, 'update_streams_dir', return_value=out))
            def render(_date, path, **kwargs):
                path.touch(); return path
            for name in ('render_recap', 'render_songs', 'render_eras'):
                stack.enter_context(patch.object(daily.cards, name, side_effect=render))
            def render_gainers(_date, period, picked, path):
                path.touch(); return path
            stack.enter_context(patch.object(daily.cards, 'render_gainers', side_effect=render_gainers))
            stack.enter_context(patch.object(daily.gainers, '_pick_gainers', return_value=[{'track_id': 'fixture'}]))
            stack.enter_context(patch.object(daily.cards.ga, 'load_album_track_map', return_value={}))
            stack.enter_context(patch.object(daily.cards.ga, 'load_history', return_value=({}, {}, {})))
            stack.enter_context(patch.object(daily.cards.ga, 'load_covers', return_value={}))
            stack.enter_context(patch.object(daily.cards.ga, 'build_album_rows', return_value=[]))
            stack.enter_context(patch.object(daily.albums, '_max_days_covered_fast', return_value=1))
            stack.enter_context(patch.object(daily.albums, 'build_tweet_with_best_day', return_value='eras'))
            stack.enter_context(patch.object(daily.records, 'build_grouped_record_reply', return_value=([], [])))
            era_builder=stack.enter_context(patch.object(daily.records, 'build_era_record_replies', return_value=([], [])))
            plan = daily.build_plan('2026-10-06', output_dir=out, preview=True)
            self.assertEqual(plan.components, ['recap', 'top20', 'top-eras', 'daily', 'weekly'])
            self.assertEqual(len(plan.posts), 5)
            self.assertIn('daily gainers', plan.posts[3][0])
            self.assertIn('weekly gainers', plan.posts[4][0])
            self.assertEqual(validate.call_args.kwargs['comparison_dates'], ('2026-10-05', '2026-09-29'))
            self.assertFalse(any(out.glob('*.lock')))
            era_image=out/'era.png';era_image.touch()
            group={'era_key':'red','album':'Red','track_ids':['era-track']}
            era_builder.return_value=([('era recap',[era_image])],[group])
            plan=daily.build_plan('2026-10-06',output_dir=out,preview=True)
            self.assertEqual(plan.posts[-1],('era recap',[era_image]))
            self.assertEqual(plan.era_groups,[group])

    def test_incomplete_history_blocks_before_rendering(self):
        with patch.object(daily.history_store, 'validate_released_active_history_complete', side_effect=RuntimeError('missing exact comparison')), patch.object(daily.cards, 'render_recap') as render:
            with self.assertRaisesRegex(RuntimeError, 'missing exact comparison'):
                daily.build_plan('2026-10-06')
            render.assert_not_called()

    def test_failure_keeps_every_lock_pending_and_success_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            assert Path(tmp).resolve().parent == Path(__file__).resolve().parent
            out=Path(tmp); image=out/'image.png'; image.touch()
            plan=daily.ThreadPlan([('recap', image)], list(daily.LOCKS), [], list(daily.LOCKS))
            with patch.object(daily, 'update_streams_dir', return_value=out), patch.object(daily, 'post_image_thread', return_value=False):
                self.assertFalse(daily.publish_plan(plan,'2026-10-06'))
                self.assertFalse(any(out.glob('*.lock')))
                self.assertFalse((out/'daily_streams_thread_posted.json').exists())
                with self.assertRaisesRegex(RuntimeError,'previous X publication'):
                    daily.publish_plan(plan,'2026-10-06')
                # Test fixture: publication is mocked and known not to have happened.
                (out/'daily_streams_thread_pending.json').unlink()
            calls=[]
            def post(posts, session, skip_if):
                if not skip_if(): calls.append(posts)
                return True
            with patch.object(daily, 'update_streams_dir', return_value=out), patch.object(daily, 'post_image_thread', side_effect=post):
                self.assertTrue(daily.publish_plan(plan,'2026-10-06'))
                self.assertTrue(daily.publish_plan(plan,'2026-10-06'))
            self.assertEqual(len(calls), 1)
            self.assertTrue(all((out/name).exists() for name in daily.LOCKS.values()))

    def test_finalize_calls_thread_once_and_removes_separate_gainers(self):
        ctx=SimpleNamespace(debug_daily_mode=False, no_post_mode=True, script_dir=Path('collectors/spotify/streams'),
                            repo_root=Path('.'), summary={'stats_date':'2026-10-06'},skip_steps=set(),best_day_last=False)
        state={}
        with patch.object(finalize,'_run') as run:
            finalize._post_daily_recap_card(ctx,state)
            finalize._post_streams_image(ctx,state)
            finalize._post_albums_daily(ctx,state)
            finalize._post_spotlight_gainers(ctx,state)
            finalize._post_weekend_song_gainers(ctx,state)
            self.assertEqual(run.call_count,1)
            self.assertTrue(any('post_daily_streams_thread.py' in str(x) for x in run.call_args.args[1]))
            self.assertIn('--no-post',run.call_args.args[1])
        self.assertEqual(finalize.PACED_EXPECTED_POSTS['gainers'],0)
        self.assertEqual(finalize.PACED_EXPECTED_POSTS['weekend-gainers'],0)

    def test_only_exceptional_scores_or_priority_records_stay_individual(self):
        rows=[{'track_id':str(i),'title':str(i),'daily_streams':300000,'kind':'since','days_since':30} for i in range(2)]
        scored={'items':[{'track_id':'0','score':58},{'track_id':'1','score':93}]}
        with patch.object(records.score_best_day_since,'score_best_day_since',return_value=scored), patch.object(records.score_best_day_since,'dynamic_early_min_score',return_value=(20,{})), patch.object(records,'load_history_for_tracks',return_value=(1000000,700000,300000,250000,200000)):
            picked=records._validated_song_rows_for_post(rows,target_date='2026-10-06',tracks_by_id={str(i):{} for i in range(2)},limit=3)
            # Track metadata must be present, even when the editorial score qualifies.
            self.assertEqual(picked,[])
            picked=records._validated_song_rows_for_post(rows,target_date='2026-10-06',tracks_by_id={str(i):{'track_id':str(i)} for i in range(2)},limit=3)
            self.assertEqual([r['track_id'] for r in picked],['1'])
        self.assertEqual(records.song_post_hard_cap('2026-10-06'),3)

    def test_failed_finalize_thread_is_not_retried_by_other_component_steps(self):
        ctx=SimpleNamespace(debug_daily_mode=False, no_post_mode=True, script_dir=Path('collectors/spotify/streams'),
                            repo_root=Path('.'), summary={'stats_date':'2026-10-06'},skip_steps=set(),best_day_last=False)
        state={}
        with patch.object(finalize,'_run',side_effect=SystemExit('X confirmation missing')) as run:
            with self.assertRaises(SystemExit):
                finalize._post_daily_recap_card(ctx,state)
            finalize._post_streams_image(ctx,state)
            finalize._post_albums_daily(ctx,state)
            finalize._post_spotlight_gainers(ctx,state)
            self.assertEqual(run.call_count,1)

    def test_old_routine_stash_cannot_bypass_new_record_policy(self):
        row={'track_id':'routine','kind':'since','days_since':30,'daily_streams':300000,'_post_score':{'score':58}}
        with patch.object(records,'_generate_best_day_since_image') as render:
            self.assertTrue(records._post_one_selected_row(row,target_date='2026-10-06',covers={},no_post=False))
            render.assert_not_called()

    def test_grouped_reply_excludes_individuals_and_blocks_inconsistent_data(self):
        rows=[{'track_id':key,'title':key,'daily_streams':300000,'kind':'since','best_day_since':'2026-08-01'} for key in ('individual','grouped')]
        with ExitStack() as stack:
            stack.enter_context(patch.object(records,'load_all_tracks',return_value=[{'track_id':r['track_id']} for r in rows]))
            stack.enter_context(patch.object(records,'_posted_track_ids_for_date',return_value=set()))
            stack.enter_context(patch.object(records,'song_posts_remaining',return_value=3))
            stack.enter_context(patch.object(records,'_pick_rows',return_value=rows))
            stack.enter_context(patch.object(records,'_validated_song_rows_for_post',return_value=[rows[0]]))
            stack.enter_context(patch.object(records,'_find_recap_rows',return_value=rows))
            stack.enter_context(patch.object(records,'load_covers',return_value={}))
            render=stack.enter_context(patch.object(records,'_generate_recap_images',return_value=[Path('test.png')]))
            with patch.object(records,'load_history_for_tracks',return_value=(1000000,700000,300000,250000,200000)):
                posts,ids=records.build_grouped_record_reply('2026-10-06',reserved_track_ids={'individual'})
                self.assertEqual(ids,['grouped']); self.assertEqual(len(posts),1)
                self.assertEqual(render.call_args.kwargs['rows'],[rows[1]])
            with patch.object(records,'load_history_for_tracks',return_value=(1000000,700000,299999,250000,200000)):
                with self.assertRaisesRegex(RuntimeError,'daily mismatch'):
                    records.build_grouped_record_reply('2026-10-06',reserved_track_ids={'individual'})

    def test_best_day_club_keeps_every_row_when_paginated(self):
        rows=[{'track_id':str(i)} for i in range(33)]
        pages=records._split_recap_rows(rows)
        self.assertLessEqual(len(pages),4)
        self.assertEqual([r for page in pages for r in page],rows)

    def test_record_pagination_fills_at_least_ten_rows(self):
        for count in (10,12,13,19,20,33,60):
            rows=list(range(count))
            pages=records._split_recap_rows(rows)
            self.assertGreaterEqual(min(map(len,pages)),10)
            self.assertEqual([r for page in pages for r in page],rows)
        self.assertEqual(len(records._split_recap_rows(list(range(12)))),1)

    def test_new_record_renderer_blocks_mismatched_daily(self):
        row={'track_id':'fixture','title':'Fixture','daily_streams':300000,'kind':'since','best_day_since':'2026-08-01'}
        with patch.object(records,'load_history_for_tracks',return_value=(1000000,700000,299999,250000,200000)), patch.object(cards,'render_best_day_club') as render:
            with self.assertRaisesRegex(RuntimeError,'incomplete or inconsistent'):
                records._generate_recap_image(rows=[row],target_date='2026-10-06',tracks_by_id={'fixture':{'track_id':'fixture','title':'Fixture'}},covers={})
            render.assert_not_called()

    def test_era_replies_use_existing_threshold_and_slugged_locks(self):
        group={'era_key':'the life of a showgirl','album':'The Life of a Showgirl',
               'items':[{'track_id':str(i),'kind':'since','best_day_since':'2026-08-01'} for i in range(3)],
               'track_ids':['0','1','2'],'count':3}
        with ExitStack() as stack:
            stack.enter_context(patch.object(records,'best_day_since_skipped',return_value=False))
            stack.enter_context(patch.object(records,'load_all_tracks',return_value=[]))
            stack.enter_context(patch.object(records,'load_covers',return_value={}))
            stack.enter_context(patch.object(records,'_era_recap_groups',return_value=[group]))
            render=stack.enter_context(patch.object(records,'_generate_recap_images',return_value=[Path('era.png')]))
            with patch.object(records,'_posted_era_recap_keys_for_date',return_value={'the_life_of_a_showgirl'}):
                self.assertEqual(records.build_era_record_replies('2026-10-06'),([],[]))
                render.assert_not_called()
            posts,groups=records.build_era_record_replies('2026-10-06',preview=True)
            self.assertEqual(len(posts),1)
            self.assertEqual(groups[0]['era_key'],group['era_key'])
            self.assertEqual(render.call_args.kwargs['era_display'],group['album'])

    def test_era_locks_written_only_after_thread_success(self):
        group={'era_key':'red','album':'Red','track_ids':['track'],'count':3}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            assert Path(tmp).resolve().parent == Path(__file__).resolve().parent
            out=Path(tmp)
            image=out/'era.png';image.touch()
            plan=daily.ThreadPlan([('era recap',[image])],[],[],[],[group])
            with patch.object(daily,'update_streams_dir',return_value=out), patch.object(daily,'post_image_thread',return_value=False), patch.object(records,'_write_era_recap_lock') as mark:
                self.assertFalse(daily.publish_plan(plan,'2026-10-06'))
                mark.assert_not_called()
            (out/'daily_streams_thread_pending.json').unlink()  # mocked publication never happened
            with patch.object(daily,'update_streams_dir',return_value=out), patch.object(daily,'post_image_thread',return_value=True), patch.object(records,'_write_era_recap_lock') as mark:
                self.assertTrue(daily.publish_plan(plan,'2026-10-06'))
                mark.assert_called_once_with('red','2026-10-06',group)


if __name__=='__main__':
    unittest.main()
