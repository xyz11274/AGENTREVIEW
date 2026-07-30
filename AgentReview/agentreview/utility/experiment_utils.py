#AgentReview/agentreview/utility/experiment_utils.py

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agentreview.agent import Player
from agentreview.paper_review_player import PaperExtractorPlayer, AreaChair, Reviewer
from agentreview.role_descriptions import get_ac_config, get_reviewer_player_config, get_author_config, \
    get_paper_extractor_config


def initialize_players(experiment_setting: dict, args):
    paper_id = experiment_setting['paper_id']
    paper_decision = experiment_setting['paper_decision']

    # ✅ 新增：单篇 md 审稿模式
    if args.task == "single_paper_review":
        # ✅ 放到这里局部导入，避免 NameError
        from agentreview.paper_review_player_md import MarkdownPaperExtractorPlayer

        players = []

        extractor_cfg = get_paper_extractor_config(
            global_settings=experiment_setting.get("global_settings", {})
        )
        extractor_cfg.setdefault("name", "Paper Extractor")

        players.append(
            MarkdownPaperExtractorPlayer(
                paper_md_path=args.paper_md_path,
                args=args,
                **extractor_cfg
            )
        )

        reviewer_cfg = get_reviewer_player_config(
            reviewer_index=1,
            global_settings=experiment_setting.get("global_settings", {}),
            **(experiment_setting["players"]["Reviewer"][0] if experiment_setting.get("players") else {})
        )
        reviewer_cfg["backend"]["model"] = args.model_name
        reviewer_cfg.setdefault("name", "Reviewer 1")

        players.append(
            Reviewer(
                data_dir=getattr(args, "data_dir", None),
                conference=getattr(args, "conference", None),
                args=args,
                **reviewer_cfg
            )
        )
        return players


    if args.task == "paper_decision":
        experiment_setting["players"] = {k: v for k, v in experiment_setting["players"].items() if k.startswith("AC")}

    players = []

    for role, players_list in experiment_setting["players"].items():

        for i, player_config in enumerate(players_list):
            if role == "AC":

                # For AC, `env_type` is either "paper_decision" or "paper_review"
                player_config = get_ac_config(env_type=args.task,
                                              scoring_method=args.ac_scoring_method,
                                              num_papers_per_area_chair=args.num_papers_per_area_chair,
                                              global_settings=experiment_setting['global_settings'],
                                              acceptance_rate=args.acceptance_rate,
                                              **player_config)

                player_config['backend']['model'] = args.model_name

                player = AreaChair(data_dir=args.data_dir,
                                   conference=args.conference,
                                   args=args,
                                   **player_config)


            elif args.task == "paper_review":


                if role == "Paper Extractor":

                    player_config = get_paper_extractor_config(global_settings=experiment_setting['global_settings'])

                    player = PaperExtractorPlayer(data_dir=args.data_dir, paper_id=paper_id,
                                                  paper_decision=paper_decision,
                                                  args=args,
                                                  conference=args.conference, **player_config)



                elif role == "Author":

                    # Author requires no behavior customization.
                    # So we directly use the Player class
                    player_config = get_author_config()
                    player = Player(data_dir=args.data_dir,
                                    conference=args.conference,
                                    args=args,
                                    **player_config)



                elif role == "Reviewer":
                    player_config = get_reviewer_player_config(reviewer_index=i + 1,
                                                               global_settings=experiment_setting['global_settings'],
                                                               **player_config)
                    player_config['backend']['model'] = args.model_name
                    player = Reviewer(data_dir=args.data_dir, conference=args.conference, args=args, **player_config)


                else:
                    raise NotImplementedError(f"Unknown role for paper review (stage 1-4): {role}")

            else:
                raise NotImplementedError(f"Unknown role for paper decision (stage 5): {role}")

            players.append(player)

    return players
