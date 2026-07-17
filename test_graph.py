from cloud_extractor import auto_wire_topology
rooms = ['living_room', 'kitchen', 'dining_room', 'bedroom', 'bedroom', 'bedroom', 'master_bedroom', 'bathroom', 'bathroom', 'bathroom', 'corridor']
wired = auto_wire_topology(rooms)
print('\nAuto-Wired Graph (with corridor):')
for r in wired:
    targets = [c['target_room'] for c in r['connections']]
    print(r['type'] + ' -> ' + str(targets))
