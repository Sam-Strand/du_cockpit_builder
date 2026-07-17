from cockpit_builder import DUConfigBuilder
import json

if __name__ == '__main__':
    builder = DUConfigBuilder(
        project_path='tests/du-autopilot',
        deploy_path='D:\My Dual Universe\Game\data\lua'
    )
    config = builder.build(deploy_to_game=True)
    
    with open('tests/config_build.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
